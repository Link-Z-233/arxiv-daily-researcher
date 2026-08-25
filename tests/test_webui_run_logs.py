import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.tabs import run_manager  # noqa: E402


class _Box:
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False


class _FakeStreamlit:
    def __init__(self):
        self.calls = []

    def container(self, *args, **kwargs):
        self.calls.append(("container", args, kwargs))
        return _Box()

    def code(self, *args, **kwargs):
        self.calls.append(("code", args, kwargs))


class RunLogViewerTests(unittest.TestCase):
    def test_selected_log_uses_an_800px_native_scroll_container(self):
        fake_st = _FakeStreamlit()
        log_path = Path("/tmp/example.log")
        with patch.object(
            run_manager, "_read_log_tail", return_value="line 1\nline 2"
        ) as read_tail, patch.object(run_manager, "st", fake_st):
            run_manager._render_log_content(log_path)

        self.assertEqual(
            fake_st.calls[0],
            ("container", (), {"height": 800, "border": True}),
        )
        self.assertEqual(fake_st.calls[1][0], "code")
        self.assertEqual(fake_st.calls[1][2]["line_numbers"], True)
        read_tail.assert_called_once_with(log_path, max_lines=300)


if __name__ == "__main__":
    unittest.main()
