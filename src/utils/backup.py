"""gzip-compressed SQLite backups with local rotation and WebDAV mirroring.

The daily-research database is the sole durable authority for the pending
queue, reader preferences and usage statistics. A backup takes a consistent
snapshot through SQLite's backup API (never a raw copy of a live WAL file),
compresses it before any network transfer, keeps a bounded number of local
copies and mirrors the same rotation to WebDAV when credentials are given.
"""

from __future__ import annotations

import gzip
import os
import sqlite3
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

BACKUP_PREFIX = "daily_research_"
BACKUP_SUFFIX = ".db.gz"
DEFAULT_BACKUP_KEEP = 5
MAX_BACKUP_KEEP = 60
_GZIP_CHUNK_BYTES = 1024 * 1024

_backup_lock = threading.Lock()


def backups_directory(data_dir: Path) -> Path:
    """Return the backup directory under the given data directory."""
    return Path(data_dir) / "backups"


def database_path(data_dir: Path) -> Path:
    """Return the canonical daily-research database path."""
    return Path(data_dir) / "daily_research" / "daily_research.db"


def list_local_backups(data_dir: Path) -> List[Dict[str, Any]]:
    """List rotated backup files newest-first with size and mtime."""
    directory = backups_directory(data_dir)
    if not directory.is_dir():
        return []
    entries = []
    for item in directory.iterdir():
        if not item.is_file() or not item.name.startswith(BACKUP_PREFIX):
            continue
        if not item.name.endswith(BACKUP_SUFFIX):
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": item.name,
                "path": str(item),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(
                    timespec="seconds"
                ),
            }
        )
    entries.sort(key=lambda entry: entry["name"], reverse=True)
    return entries


def _create_consistent_snapshot(database: Path, target: Path) -> None:
    source_conn = sqlite3.connect(str(database))
    snapshot_conn = sqlite3.connect(str(target))
    try:
        source_conn.backup(snapshot_conn)
    finally:
        snapshot_conn.close()
        source_conn.close()


def _compress_file(source: Path, target: Path) -> None:
    with open(source, "rb") as raw, gzip.open(target, "wb", compresslevel=6) as packed:
        while True:
            chunk = raw.read(_GZIP_CHUNK_BYTES)
            if not chunk:
                break
            packed.write(chunk)


def _rotate_local(directory: Path, keep: int) -> List[str]:
    """Remove the oldest backups beyond ``keep``; return the removed names."""
    if keep <= 0:
        return []
    survivors = list_local_backups(directory.parent)
    doomed = survivors[keep:]
    removed = []
    for entry in doomed:
        try:
            os.remove(entry["path"])
            removed.append(entry["name"])
        except OSError:
            continue
    return removed


def _remote_backup_names(webdav_sync: Any, remote_dir: str) -> List[str]:
    """Return backup file names currently present in the remote directory."""
    try:
        entries = webdav_sync.client.list(remote_dir)
    except Exception:
        return []
    names = []
    for entry in entries or []:
        if isinstance(entry, dict):
            name = str(entry.get("name") or "")
        else:
            name = str(entry or "")
        name = name.strip().strip("/").rsplit("/", 1)[-1]
        if name.startswith(BACKUP_PREFIX) and name.endswith(BACKUP_SUFFIX):
            names.append(name)
    return sorted(names, reverse=True)


def _upload_and_rotate_remote(
    webdav_sync: Any, local_path: Path, keep: int, logger: Any = None
) -> Dict[str, Any]:
    """Upload one backup and prune the remote rotation; best-effort."""
    result: Dict[str, Any] = {"uploaded": False, "remote_path": None, "pruned": []}
    remote_dir = webdav_sync._remote("data/backups")
    if not webdav_sync._ensure_remote_dir(remote_dir + "/"):
        raise RuntimeError("无法创建 WebDAV 备份目录")
    remote_file = f"{remote_dir}/{local_path.name}"
    webdav_sync.client.upload_file(remote_file, str(local_path))
    result["uploaded"] = True
    result["remote_path"] = remote_file
    for name in _remote_backup_names(webdav_sync, remote_dir)[keep:]:
        try:
            webdav_sync.client.delete(f"{remote_dir}/{name}")
            result["pruned"].append(name)
        except Exception as exc:
            if logger:
                logger.warning("[Backup] 远端备份轮转删除失败 %s: %s", name, exc)
    return result


def create_backup(
    data_dir: Path,
    *,
    keep: int = DEFAULT_BACKUP_KEEP,
    webdav_sync: Any = None,
    logger: Any = None,
) -> Dict[str, Any]:
    """Create one compressed backup; rotate local (and remote) copies.

    ``webdav_sync`` may be any object exposing the small WebDAVSync surface
    (``client.upload_file``/``client.list``/``client.delete``, ``_remote``,
    ``_ensure_remote_dir``); passing ``None`` keeps the backup local-only.
    An upload failure never discards the local snapshot.
    """
    if _backup_lock.locked():
        return {"created": False, "reason": "backup_already_running"}

    database = database_path(data_dir)
    if not database.exists():
        return {"created": False, "reason": "database_missing"}

    bounded_keep = max(1, min(int(keep), MAX_BACKUP_KEEP))
    directory = backups_directory(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    with _backup_lock:
        snapshot_fd, snapshot_name = tempfile.mkstemp(
            dir=str(directory), prefix=".snapshot_", suffix=".sqlite"
        )
        os.close(snapshot_fd)
        snapshot_path = Path(snapshot_name)
        archive_path = directory / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
        if archive_path.exists():
            # 同一秒内的手动+自动备份不应互相覆盖；追加序号保证唯一。
            for sequence in range(2, 100):
                archive_path = directory / (
                    f"{BACKUP_PREFIX}{stamp}_{sequence}{BACKUP_SUFFIX}"
                )
                if not archive_path.exists():
                    break
        try:
            _create_consistent_snapshot(database, snapshot_path)
            _compress_file(snapshot_path, archive_path)
        finally:
            snapshot_path.unlink(missing_ok=True)

        result: Dict[str, Any] = {
            "created": True,
            "path": str(archive_path),
            "name": archive_path.name,
            "size_bytes": archive_path.stat().st_size,
            "local_rotated": _rotate_local(directory, bounded_keep),
            "uploaded": False,
            "remote_path": None,
        }

        if webdav_sync is not None:
            try:
                result.update(
                    _upload_and_rotate_remote(
                        webdav_sync, archive_path, bounded_keep, logger=logger
                    )
                )
            except Exception as exc:
                result["upload_error"] = str(exc)
                if logger:
                    logger.warning("[Backup] WebDAV 上传失败（本地备份已保留）: %s", exc)
        return result


def run_scheduled_backup(logger: Any = None) -> Optional[Dict[str, Any]]:
    """Worker hook after a daily run; honours the ``backup`` config section."""
    from config import settings

    if not getattr(settings, "BACKUP_ENABLED", False):
        return None

    webdav_sync = None
    if getattr(settings, "BACKUP_UPLOAD_TO_WEBDAV", True) and getattr(
        settings, "WEBDAV_ENABLED", False
    ):
        from utils.webdav_sync import create_sync_client

        webdav_sync = create_sync_client()

    return create_backup(
        Path(settings.DATA_DIR),
        keep=getattr(settings, "BACKUP_KEEP", DEFAULT_BACKUP_KEEP),
        webdav_sync=webdav_sync,
        logger=logger,
    )
