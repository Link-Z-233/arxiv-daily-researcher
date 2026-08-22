"""gzip 数据库备份：一致性快照、本地轮转、WebDAV 上传与远端轮转。"""

from __future__ import annotations

import gzip
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.backup import (  # noqa: E402
    BACKUP_SUFFIX,
    create_backup,
    list_local_backups,
    run_scheduled_backup,
)
from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _seed_database(data_dir: Path) -> None:
    store = DailyResearchStore(data_dir / "daily_research" / "daily_research.db")
    store.set_app_state("seed", "v1")


class _FakeWebDAVClient:
    def __init__(self, existing=()):
        self.remote_files = set(existing)
        self.uploads = []
        self.deletes = []

    def upload_file(self, remote_path, local_path):
        self.remote_files.add(remote_path.rsplit("/", 1)[-1])
        self.uploads.append((remote_path, local_path))

    def list(self, remote_dir):
        return sorted(self.remote_files)

    def delete(self, remote_path):
        self.remote_files.discard(remote_path.rsplit("/", 1)[-1])
        self.deletes.append(remote_path)


class _FakeWebDAVSync:
    def __init__(self, existing=()):
        self.client = _FakeWebDAVClient(existing)
        self.ensured = []

    def _remote(self, rel_path):
        return f"/remote/{rel_path}"

    def _ensure_remote_dir(self, remote_dir):
        self.ensured.append(remote_dir)
        return True


class BackupCreationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        _seed_database(self.data_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def test_backup_is_valid_gzip_sqlite(self):
        result = create_backup(self.data_dir)
        self.assertTrue(result["created"])
        backup_path = Path(result["path"])
        self.assertTrue(backup_path.name.endswith(BACKUP_SUFFIX))

        with gzip.open(backup_path, "rb") as packed:
            restored = backup_path.with_suffix(".restored")
            restored.write_bytes(packed.read())
        conn = sqlite3.connect(str(restored))
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            value = conn.execute(
                "SELECT value FROM app_state WHERE key = 'seed'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIn("daily_papers", tables)
        self.assertEqual(value, ("v1",))

    def test_local_rotation_keeps_newest(self):
        for _ in range(4):
            create_backup(self.data_dir, keep=2)

        survivors = list_local_backups(self.data_dir)
        self.assertEqual(len(survivors), 2)
        names = [entry["name"] for entry in survivors]
        self.assertEqual(names, sorted(names, reverse=True))

    def test_missing_database_reports_reason(self):
        empty = self.data_dir / "empty"
        empty.mkdir()
        result = create_backup(empty)
        self.assertFalse(result["created"])
        self.assertEqual(result["reason"], "database_missing")

    def test_upload_and_remote_rotation(self):
        fake = _FakeWebDAVSync(
            existing={
                "daily_research_20200101_000000.db.gz",
                "daily_research_20200102_000000.db.gz",
            }
        )
        result = create_backup(self.data_dir, keep=2, webdav_sync=fake)

        self.assertTrue(result["created"])
        self.assertTrue(result["uploaded"])
        self.assertEqual(len(fake.client.uploads), 1)
        # 新备份 + 两个旧备份 = 3 个，保留 2 个，应删除最旧的 1 个
        self.assertEqual(len(fake.client.deletes), 1)
        self.assertIn("20200101", fake.client.deletes[0])
        self.assertNotIn(
            "daily_research_20200101_000000.db.gz", fake.client.remote_files
        )

    def test_upload_failure_keeps_local_backup(self):
        fake = _FakeWebDAVSync()
        fake.client.upload_file = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("network down")
        )
        result = create_backup(self.data_dir, webdav_sync=fake)

        self.assertTrue(result["created"])
        self.assertFalse(result["uploaded"])
        self.assertIn("upload_error", result)
        self.assertTrue(Path(result["path"]).exists())

    def test_no_temp_snapshot_left_behind(self):
        create_backup(self.data_dir)
        leftovers = [
            item.name
            for item in (self.data_dir / "backups").iterdir()
            if item.name.startswith(".snapshot_")
        ]
        self.assertEqual(leftovers, [])


class ScheduledBackupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        _seed_database(self.data_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def _install_settings(self, attrs):
        import config as config_module

        self._original_settings = config_module.settings
        holder = type("Holder", (), {"__getattr__": lambda self, name: attrs[name]})
        config_module.settings = holder()

    def tearDownSettings(self):
        import config as config_module

        if hasattr(self, "_original_settings"):
            config_module.settings = self._original_settings

    def test_disabled_returns_none(self):
        self._install_settings(
            {
                "BACKUP_ENABLED": False,
                "DATA_DIR": self.data_dir,
                "BACKUP_KEEP": 5,
                "BACKUP_UPLOAD_TO_WEBDAV": True,
                "WEBDAV_ENABLED": False,
            }
        )
        try:
            self.assertIsNone(run_scheduled_backup())
        finally:
            self.tearDownSettings()

    def test_enabled_creates_local_backup(self):
        self._install_settings(
            {
                "BACKUP_ENABLED": True,
                "DATA_DIR": self.data_dir,
                "BACKUP_KEEP": 3,
                "BACKUP_UPLOAD_TO_WEBDAV": False,
                "WEBDAV_ENABLED": False,
            }
        )
        try:
            result = run_scheduled_backup()
        finally:
            self.tearDownSettings()
        self.assertIsNotNone(result)
        self.assertTrue(result["created"])
        self.assertFalse(result["uploaded"])


if __name__ == "__main__":
    unittest.main()


class BackupImportExportTests(unittest.TestCase):
    def test_export_zip_round_trip_restores_and_archives_previous(self):
        import io
        import zipfile

        from utils.backup import export_backup_zip, restore_backup_archive

        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            _seed_database(data_dir)

            bundle, filename = export_backup_zip(data_dir)
            self.assertTrue(filename.endswith(".zip"))
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                self.assertIn("daily_research.db", archive.namelist())

            # 在导出后写入新数据，导入应回滚到导出时的快照并存档当前库
            store = DailyResearchStore(
                data_dir / "daily_research" / "daily_research.db"
            )
            store.set_app_state("seed", "v2")

            result = restore_backup_archive(data_dir, bundle, filename)
            self.assertTrue(result["restored"])
            self.assertEqual(result["source_member"], "daily_research.db")
            self.assertIsNotNone(result["archived_previous"])

            restored = DailyResearchStore(data_dir / "daily_research" / "daily_research.db")
            self.assertEqual(restored.get_app_state("seed"), "v1")
            archived = sqlite3.connect(result["archived_previous"])
            archived_state = archived.execute(
                "SELECT value FROM app_state WHERE key='seed'"
            ).fetchone()[0]
            archived.close()
            self.assertEqual(archived_state, "v2")

    def test_restore_auto_detects_gzip_and_raw_sqlite(self):
        import gzip as gzip_mod

        from utils.backup import create_backup, restore_backup_archive

        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            _seed_database(data_dir)
            created = create_backup(data_dir, keep=5)
            self.assertTrue(created["created"])

            with TemporaryDirectory() as other_dir:
                other = Path(other_dir)
                _seed_database(other)
                store = DailyResearchStore(other / "daily_research" / "daily_research.db")
                store.set_app_state("seed", "changed")

                packed = Path(created["path"]).read_bytes()  # 已是 gzip
                result = restore_backup_archive(
                    other, packed, created["name"]
                )
                self.assertTrue(result["restored"])
                restored = DailyResearchStore(
                    other / "daily_research" / "daily_research.db"
                )
                self.assertEqual(restored.get_app_state("seed"), "v1")

                raw = Path(created["path"]).read_bytes()
                raw_db = gzip_mod.decompress(raw)
                result2 = restore_backup_archive(other, raw_db, "daily_research.db")
                self.assertTrue(result2["restored"])
                self.assertEqual(
                    DailyResearchStore(
                        other / "daily_research" / "daily_research.db"
                    ).get_app_state("seed"),
                    "v1",
                )

    def test_restore_rejects_invalid_payload(self):
        from utils.backup import restore_backup_archive

        with TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                restore_backup_archive(
                    Path(temp_dir), b"not a database at all", "junk.zip"
                )
            import io
            import zipfile

            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("readme.txt", "no db here")
            with self.assertRaises(ValueError):
                restore_backup_archive(
                    Path(temp_dir), buffer.getvalue(), "empty.zip"
                )
