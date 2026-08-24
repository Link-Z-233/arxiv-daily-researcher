"""gzip-compressed SQLite backups with local rotation and WebDAV mirroring.

The daily-research database is the sole durable authority for the pending
queue, reader preferences and usage statistics. A backup takes a consistent
snapshot through SQLite's backup API (never a raw copy of a live WAL file)
and compresses it before any network transfer. Local copies rotate strictly
by the configured age window (seven days by default; zero keeps them forever).
The WebDAV mirror is
incremental: an archive is uploaded only when the database content actually
changed since the last upload, and remote copies are never deleted, so the
remote directory is a permanent archive of every content state.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

BACKUP_PREFIX = "daily_research_"
BACKUP_SUFFIX = ".db.gz"
LOCAL_BACKUP_RETENTION_DAYS = 7
MIN_LOCAL_BACKUP_RETENTION_DAYS = 0
_GZIP_CHUNK_BYTES = 1024 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024
_BACKUP_NAME_RE = re.compile(r"^daily_research_(\d{8}_\d{6})(?:_\d+)?\.db\.gz$")
_UPLOAD_STATE_FILENAME = "webdav_upload_state.json"

_backup_lock = threading.Lock()


def validate_local_backup_retention_days(value: Any) -> int:
    """Return a safe local-backup retention window in whole days.

    ``0`` explicitly means keep local backups forever.  Positive values are
    age windows in days; no artificial upper bound is imposed.  WebDAV
    retention remains intentionally unlimited and is unaffected.
    """
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < MIN_LOCAL_BACKUP_RETENTION_DAYS
    ):
        raise ValueError(
            "backup.local_retention_days 必须是 "
            f"大于或等于 {MIN_LOCAL_BACKUP_RETENTION_DAYS} 的整数（0 表示永久保留）"
        )
    return value


def backups_directory(data_dir: Path) -> Path:
    """Return the backup directory under the given data directory."""
    return Path(data_dir) / "backups"


def database_path(data_dir: Path) -> Path:
    """Return the canonical daily-research database path."""
    return Path(data_dir) / "daily_research" / "daily_research.db"


def _backup_database_path(data_dir: Path, database: Optional[Path]) -> Path:
    """Resolve an explicit configured SQLite path or the legacy default."""
    return Path(database) if database is not None else database_path(data_dir)


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


def _backup_timestamp(name: str) -> Optional[datetime]:
    """Return the creation timestamp encoded in a backup file name."""
    match = _BACKUP_NAME_RE.match(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def _rotate_local(directory: Path, retention_days: int) -> List[str]:
    """Remove backups older than ``retention_days``; return the removed names.

    Rotation is strictly age-based (filename timestamp = creation time) and
    never touches files whose name cannot be dated — an undated archive is
    preserved forever rather than guessed at. ``retention_days=0`` disables
    local rotation entirely.
    """
    # ``0`` is the user-facing "keep forever" setting.  Returning before
    # calculating a zero-day cutoff also guarantees that a newly created
    # archive can never be deleted in the same backup operation.
    if retention_days == 0:
        return []
    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = []
    for item in directory.iterdir():
        if not item.is_file() or not item.name.endswith(BACKUP_SUFFIX):
            continue
        stamp = _backup_timestamp(item.name)
        if stamp is None or stamp >= cutoff:
            continue
        try:
            os.remove(item)
            removed.append(item.name)
        except OSError:
            continue
    return removed


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as raw:
        while True:
            chunk = raw.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _upload_state_path(directory: Path) -> Path:
    return directory / _UPLOAD_STATE_FILENAME


def _load_upload_state(directory: Path) -> Dict[str, str]:
    try:
        data = json.loads(_upload_state_path(directory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_upload_state(directory: Path, state: Dict[str, str]) -> None:
    _upload_state_path(directory).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _remote_backup_names(webdav_sync: Any, remote_dir: str) -> Optional[List[str]]:
    """Return remote backup names, or ``None`` when the directory cannot be read."""
    try:
        entries = webdav_sync.client.list(remote_dir)
    except Exception:
        return None
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


def _upload_incremental_remote(
    webdav_sync: Any,
    local_path: Path,
    snapshot_hash: str,
    state_directory: Path,
    logger: Any = None,
) -> Dict[str, Any]:
    """Mirror one backup to WebDAV incrementally; best-effort.

    The upload is skipped when the remote already holds this exact file name
    or when the database content hash is unchanged since the last successful
    upload. Remote files are never deleted — the WebDAV directory is a
    permanent archive, not a rotation.
    """
    result: Dict[str, Any] = {
        "uploaded": False,
        "remote_path": None,
        "skipped_reason": None,
    }
    remote_dir = webdav_sync._remote("data/backups")
    if not webdav_sync._ensure_remote_dir(remote_dir + "/"):
        raise RuntimeError("无法创建 WebDAV 备份目录")
    remote_file = f"{remote_dir}/{local_path.name}"

    remote_names = _remote_backup_names(webdav_sync, remote_dir)
    if remote_names is not None and local_path.name in remote_names:
        # A previous run uploaded this archive but crashed before recording
        # the state; treat it as mirrored instead of re-uploading.
        result["skipped_reason"] = "already_on_remote"
        result["remote_path"] = remote_file
        return result

    state = _load_upload_state(state_directory)
    recorded_remote_name = state.get("remote_name")
    if (
        state.get("hash") == snapshot_hash
        and recorded_remote_name
        # A temporary list failure must not turn a healthy incremental backup
        # into a duplicate upload.  When listing succeeds, however, verify
        # that the last recorded remote archive still exists; otherwise a
        # manually deleted/lost WebDAV file must be repaired.
        and (remote_names is None or recorded_remote_name in remote_names)
    ):
        result["skipped_reason"] = "content_unchanged"
        result["remote_path"] = recorded_remote_name
        return result

    webdav_sync.client.upload_file(remote_file, str(local_path))
    _save_upload_state(
        state_directory,
        {"hash": snapshot_hash, "remote_name": local_path.name},
    )
    result["uploaded"] = True
    result["remote_path"] = remote_file
    return result


def create_backup(
    data_dir: Path,
    *,
    database: Optional[Path] = None,
    retention_days: int = LOCAL_BACKUP_RETENTION_DAYS,
    webdav_sync: Any = None,
    logger: Any = None,
) -> Dict[str, Any]:
    """Create one compressed backup; rotate local copies by age.

    ``webdav_sync`` may be any object exposing the small WebDAVSync surface
    (``client.upload_file``/``client.list``, ``_remote``, ``_ensure_remote_dir``);
    passing ``None`` keeps the backup local-only. An upload failure never
    discards the local snapshot. ``database`` lets installations that keep
    their SQLite file outside the conventional ``data_dir/daily_research``
    location snapshot the exact configured database while still storing local
    archives under ``data_dir/backups``.
    """
    retention_days = validate_local_backup_retention_days(retention_days)
    if _backup_lock.locked():
        return {"created": False, "reason": "backup_already_running"}

    selected_database = _backup_database_path(data_dir, database)
    if not selected_database.exists():
        return {"created": False, "reason": "database_missing"}

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
            _create_consistent_snapshot(selected_database, snapshot_path)
            snapshot_hash = _file_sha256(snapshot_path)
            _compress_file(snapshot_path, archive_path)
        finally:
            snapshot_path.unlink(missing_ok=True)

        result: Dict[str, Any] = {
            "created": True,
            "path": str(archive_path),
            "name": archive_path.name,
            "size_bytes": archive_path.stat().st_size,
            "local_rotated": _rotate_local(directory, retention_days),
            "uploaded": False,
            "remote_path": None,
        }

        if webdav_sync is not None:
            try:
                result.update(
                    _upload_incremental_remote(
                        webdav_sync,
                        archive_path,
                        snapshot_hash,
                        directory,
                        logger=logger,
                    )
                )
            except Exception as exc:
                result["upload_error"] = str(exc)
                if logger:
                    logger.warning("[Backup] WebDAV 上传失败（本地备份已保留）: %s", exc)
        return result


def run_scheduled_backup(logger: Any = None) -> Optional[Dict[str, Any]]:
    """Worker hook after a daily run; honours the ``backup`` config section.

    备份始终先压缩再上传；只要 WebDAV 已启用并配置了凭据就镜像上传
    （增量：内容未变化时跳过），本地按配置的保留天数轮转。
    """
    from config import settings

    if not getattr(settings, "BACKUP_ENABLED", False):
        return None

    webdav_sync = None
    if getattr(settings, "WEBDAV_ENABLED", False):
        from utils.webdav_sync import create_sync_client

        webdav_sync = create_sync_client()

    retention_days = validate_local_backup_retention_days(
        getattr(
            settings,
            "BACKUP_LOCAL_RETENTION_DAYS",
            LOCAL_BACKUP_RETENTION_DAYS,
        )
    )
    return create_backup(
        Path(settings.DATA_DIR),
        database=Path(settings.DAILY_RESEARCH_DB_PATH),
        retention_days=retention_days,
        webdav_sync=webdav_sync,
        logger=logger,
    )


# ─── 手动导入 / 导出（都是压缩包，自动识别内容）─────────────────────────────

_SQLITE_HEADER = b"SQLite format 3\x00"
_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")


def export_backup_zip(
    data_dir: Path, *, database: Optional[Path] = None
) -> tuple[bytes, str]:
    """把当前数据库的一致性快照打包为 zip，返回 (压缩包字节, 文件名)。"""
    import io
    import zipfile

    selected_database = _backup_database_path(data_dir, database)
    if not selected_database.exists():
        raise FileNotFoundError("数据库不存在，无法导出")

    snapshot_fd, snapshot_name = tempfile.mkstemp(suffix=".sqlite")
    os.close(snapshot_fd)
    snapshot_path = Path(snapshot_name)
    try:
        _create_consistent_snapshot(selected_database, snapshot_path)
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
    data_dir: Path,
    data: bytes,
    filename: str = "import",
    *,
    database: Optional[Path] = None,
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

        selected_database = _backup_database_path(data_dir, database)
        archived: Optional[Path] = None
        if selected_database.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archived = backups_directory(data_dir) / f"pre_import_{stamp}.db"
            archived.parent.mkdir(parents=True, exist_ok=True)
            _create_consistent_snapshot(selected_database, archived)
        else:
            selected_database.parent.mkdir(parents=True, exist_ok=True)

        staged_fd, staged_name = tempfile.mkstemp(
            dir=str(selected_database.parent), suffix=".import"
        )
        os.close(staged_fd)
        staged = Path(staged_name)
        try:
            staged.write_bytes(database_bytes)
            # 旧库的 WAL/SHM 属于旧文件的派生状态，残留会导致新库被旧日志回放。
            os.replace(staged, selected_database)
            for suffix in ("-wal", "-shm"):
                Path(str(selected_database) + suffix).unlink(missing_ok=True)
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
