import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.tabs import run_manager  # noqa: E402


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeStreamlit:
    def __init__(self, *, auto_refresh=True):
        self.session_state = {}
        self.auto_refresh = auto_refresh
        self.toggle_calls = []

    def toggle(self, *args, **kwargs):
        self.toggle_calls.append((args, kwargs))
        return self.auto_refresh

    def container(self, **_kwargs):
        return _Context()

    def button(self, *_args, **_kwargs):
        return False


class RunAutoRefreshTests(unittest.TestCase):
    def test_control_is_rendered_by_status_panel_not_run_button_area(self):
        fake_st = _FakeStreamlit()
        with (
            patch.object(run_manager, "st", fake_st),
            patch.object(run_manager, "_get_all_running_locks", return_value=[]),
            patch.object(
                run_manager,
                "_trigger_queue_state",
                return_value=(None, False, False),
            ),
            patch.object(run_manager, "_render_status_snapshot"),
        ):
            run_manager._render_run_control()
            self.assertEqual(fake_st.toggle_calls, [])
            run_manager._render_status_panel({})

        self.assertEqual(len(fake_st.toggle_calls), 1)
        self.assertEqual(fake_st.toggle_calls[0][1]["key"], "rm_auto_refresh_on")

    def test_auto_refresh_fragment_only_mounts_for_a_running_task(self):
        fake_st = _FakeStreamlit(auto_refresh=True)
        live_fragment = Mock()
        snapshot = Mock()

        with (
            patch.object(run_manager, "st", fake_st),
            patch.object(run_manager, "_live_status_fragment", live_fragment),
            patch.object(run_manager, "_render_status_snapshot", snapshot),
            patch.object(run_manager, "_get_all_running_locks", return_value=[]),
            patch.object(run_manager, "_trigger_queue_state", return_value=(None, False, False)),
        ):
            run_manager._render_status_panel({"daily_max_papers_per_run": 5})

        live_fragment.assert_not_called()
        snapshot.assert_called_once_with({"daily_max_papers_per_run": 5})

        fake_st = _FakeStreamlit(auto_refresh=True)
        live_fragment = Mock()
        snapshot = Mock()
        with (
            patch.object(run_manager, "st", fake_st),
            patch.object(run_manager, "_live_status_fragment", live_fragment),
            patch.object(run_manager, "_render_status_snapshot", snapshot),
            patch.object(
                run_manager,
                "_get_all_running_locks",
                return_value=[(Path("run.lock"), 1)],
            ),
            patch.object(run_manager, "_trigger_queue_state", return_value=(None, False, False)),
        ):
            run_manager._render_status_panel({})

        live_fragment.assert_called_once_with()
        snapshot.assert_not_called()

    def test_disabled_auto_refresh_keeps_a_running_task_static(self):
        fake_st = _FakeStreamlit(auto_refresh=False)
        live_fragment = Mock()
        snapshot = Mock()
        with (
            patch.object(run_manager, "st", fake_st),
            patch.object(run_manager, "_live_status_fragment", live_fragment),
            patch.object(run_manager, "_render_status_snapshot", snapshot),
            patch.object(
                run_manager,
                "_get_all_running_locks",
                return_value=[(Path("run.lock"), 1)],
            ),
            patch.object(run_manager, "_trigger_queue_state", return_value=(None, False, False)),
        ):
            run_manager._render_status_panel({})

        live_fragment.assert_not_called()
        snapshot.assert_called_once_with({})

    def test_recent_trigger_keeps_polling_until_worker_creates_run_lock(self):
        """A WebUI launch should not lose auto-refresh during watcher pickup."""
        fake_st = _FakeStreamlit(auto_refresh=True)
        live_fragment = Mock()
        snapshot = Mock()

        with (
            patch.object(run_manager, "st", fake_st),
            patch.object(run_manager, "_live_status_fragment", live_fragment),
            patch.object(run_manager, "_render_status_snapshot", snapshot),
            patch.object(run_manager, "_get_all_running_locks", return_value=[]),
            patch.object(run_manager, "_trigger_queue_state", return_value=(2.0, True, False)),
        ):
            run_manager._render_status_panel({})

        live_fragment.assert_called_once_with()
        snapshot.assert_not_called()

    def test_stale_trigger_does_not_keep_idle_panel_polling(self):
        fake_st = _FakeStreamlit(auto_refresh=True)
        live_fragment = Mock()
        snapshot = Mock()

        with (
            patch.object(run_manager, "st", fake_st),
            patch.object(run_manager, "_live_status_fragment", live_fragment),
            patch.object(run_manager, "_render_status_snapshot", snapshot),
            patch.object(run_manager, "_get_all_running_locks", return_value=[]),
            patch.object(run_manager, "_trigger_queue_state", return_value=(31.0, False, True)),
        ):
            run_manager._render_status_panel({})

        live_fragment.assert_not_called()
        snapshot.assert_called_once_with({})


class ConfiguredRunLockPathTests(unittest.TestCase):
    def test_status_discovers_locks_in_custom_data_dir(self):
        """The Docker trigger root and worker data root can intentionally differ."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            default_dir = root / "data" / "run"
            configured_data_dir = root / "custom-state"
            configured_dir = configured_data_dir / "run"
            default_dir.mkdir(parents=True)
            configured_dir.mkdir(parents=True)
            default_lock = default_dir / "legacy_import.lock"
            configured_lock = configured_dir / "daily_research.lock"
            default_lock.write_text("PID=1", encoding="utf-8")
            configured_lock.write_text("PID=2", encoding="utf-8")

            with (
                patch.object(run_manager, "_LOCK_DIR", default_dir),
                patch.object(
                    run_manager,
                    "_configured_worker_lock_dir",
                    return_value=configured_dir,
                ),
                patch.object(
                    run_manager,
                    "_is_lock_held",
                    side_effect=lambda path: path == configured_lock,
                ),
            ):
                files = run_manager._get_lock_files()
                running = run_manager._get_all_running_locks()

            self.assertEqual(set(files), {default_lock, configured_lock})
            self.assertEqual(running, [(configured_lock, 2)])

    def test_configured_worker_lock_dir_uses_paths_data_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            configured_data_dir = Path(temp_dir) / "state"
            with (
                patch(
                    "utils.config_io.read_config_json",
                    return_value={"paths": {"data_dir": "data/state"}},
                ),
                patch(
                    "utils.config_io._resolve_project_relative_config_path",
                    return_value=configured_data_dir,
                ),
            ):
                self.assertEqual(
                    run_manager._configured_worker_lock_dir(),
                    configured_data_dir / "run",
                )


class LiveWorkerLogSelectionTests(unittest.TestCase):
    def test_legacy_lock_selects_real_import_log_over_outer_manual_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logs = Path(temp_dir)
            outer = logs / "manual_20260826_120000.log"
            actual = logs / "legacy_import_20260826_120001.log"
            outer.write_text("watcher only\n", encoding="utf-8")
            actual.write_text("import progress\n", encoding="utf-8")

            with patch.object(run_manager, "_LOGS_DIR", logs):
                chosen = run_manager._latest_run_log(
                    [(Path("/run/legacy_import.lock"), 123)],
                    {"run_kind": "legacy_import"},
                )

            self.assertEqual(chosen, actual)

    def test_visible_active_run_beats_waiting_import_lock(self):
        locks = [
            (Path("/run/legacy_import.lock"), 10),
            (Path("/run/daily_research.lock"), 20),
        ]

        chosen = run_manager._primary_running_lock(
            locks, {"run_kind": "daily"}
        )

        self.assertEqual(chosen, locks[1])

    def test_import_wait_without_database_heartbeat_shows_other_active_task(self):
        locks = [
            (Path("/run/legacy_import.lock"), 10),
            (Path("/run/trend_research_123.lock"), 20),
        ]

        chosen = run_manager._primary_running_lock(locks, None)

        self.assertEqual(chosen, locks[1])

if __name__ == "__main__":
    unittest.main()
