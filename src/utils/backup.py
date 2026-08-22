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
    """Worker hook after a daily run; honours the ``backup`` config section.

    备份始终先压缩再上传；只要 WebDAV 已启用并配置了凭据就默认镜像上传，
    不再提供单独的上传开关。
    """
    from config import settings

    if not getattr(settings, "BACKUP_ENABLED", False):
        return None

    webdav_sync = None
    if getattr(settings, "WEBDAV_ENABLED", False):
        from utils.webdav_sync import create_sync_client

        webdav_sync = create_sync_client()

    return create_backup(
        Path(settings.DATA_DIR),
        keep=getattr(settings, "BACKUP_KEEP", DEFAULT_BACKUP_KEEP),
        webdav_sync=webdav_sync,
        logger=logger,
    )


# ─── 手动导入 / 导出（都是压缩包，自动识别内容）─────────────────────────────

_SQLITE_HEADER = b"SQLite format 3\x00"
_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")


def export_backup_zip(data_dir: Path) -> tuple[bytes, str]:
    """把当前数据库的一致性快照打包为 zip，返回 (压缩包字节, 文件名)。"""
    import io
    import zipfile

    database = database_path(data_dir)
    if not database.exists():
        raise FileNotFoundError("数据库不存在，无法导出")

    snapshot_fd, snapshot_name = tempfile.mkstemp(suffix=".sqlite")
    os.close(snapshot_fd)
    snapshot_path = Path(snapshot_name)
    try:
        _create_consistent_snapshot(database, snapshot_path)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot_path, arcname="daily_research.db")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return buffer.getvalue(), f"daily_research_export_{stamp}.zip"
    finally:
        snapshot_path.unlink(missing_ok=True)


def _extract_database_bytes(data: bytes, filename: str) -> tuple[bytes, str]:
    """从 zip / gzip / 原始 SQLite 文件自动提取数据库内容。"""
    import gzip as gzip_module
    import io
    import zipfile as zipfile_module

    if zipfile_module.is_zipfile(io.BytesIO(data)):
        with zipfile_module.ZipFile(io.BytesIO(data)) as archive:
            members = [name for name in archive.namelist() if not name.endswith("/")]
            pool = [
                name
                for name in members
                if Path(name).name == "daily_research.db"
                or Path(name).suffix.lower() in _DB_SUFFIXES
            ]
            if not pool:
                raise ValueError("压缩包里没有找到数据库文件（.db/.sqlite/.sqlite3）")
            member = sorted(pool)[0]
            return archive.read(member), member
    if data[:2] == b"\x1f\x8b":
        return gzip_module.decompress(data), filename
    return data, filename


def restore_backup_archive(
    data_dir: Path, data: bytes, filename: str = "import"
) -> Dict[str, Any]:
    """把导入的压缩包恢复为当前数据库。

    自动识别 zip（取 daily_research.db 或首个 .db/.sqlite 成员）、gzip
    （本地轮转备份格式）与原始 SQLite 文件。导入前做完整性校验；原数据库
    先通过一致性快照存档（连同 WAL 中已提交的内容），绝不删除数据。
    """
    database_bytes, source = _extract_database_bytes(data, filename)
    if database_bytes[:16] != _SQLITE_HEADER:
        raise ValueError(f"导入内容不是有效的 SQLite 数据库（来源：{source}）")

    verify_fd, verify_name = tempfile.mkstemp(suffix=".sqlite")
    os.close(verify_fd)
    verify_path = Path(verify_name)
    try:
        verify_path.write_bytes(database_bytes)
        conn = sqlite3.connect(str(verify_path))
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
        if not row or str(row[0]).lower() != "ok":
            detail = row[0] if row else "unknown"
            raise ValueError(f"导入数据库完整性校验未通过：{detail}")

        database = database_path(data_dir)
        archived: Optional[Path] = None
        if database.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archived = backups_directory(data_dir) / f"pre_import_{stamp}.db"
            archived.parent.mkdir(parents=True, exist_ok=True)
            _create_consistent_snapshot(database, archived)
        else:
            database.parent.mkdir(parents=True, exist_ok=True)

        staged_fd, staged_name = tempfile.mkstemp(
            dir=str(database.parent), suffix=".import"
        )
        os.close(staged_fd)
        staged = Path(staged_name)
        try:
            staged.write_bytes(database_bytes)
            # 旧库的 WAL/SHM 属于旧文件的派生状态，残留会导致新库被旧日志回放。
            os.replace(staged, database)
            for suffix in ("-wal", "-shm"):
                Path(str(database) + suffix).unlink(missing_ok=True)
        finally:
            staged.unlink(missing_ok=True)

        return {
            "restored": True,
            "source_member": source,
            "size_bytes": len(database_bytes),
            "archived_previous": str(archived) if archived else None,
        }
    finally:
        verify_path.unlink(missing_ok=True)
