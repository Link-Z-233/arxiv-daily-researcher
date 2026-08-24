import sys
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


if __name__ == "__main__":
    unittest.main()
