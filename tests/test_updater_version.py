import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils import updater  # noqa: E402
from utils.updater import _is_remote_newer, _parse_version  # noqa: E402


class VersionComparisonTests(unittest.TestCase):
    def test_parse_simple_semver(self):
        self.assertEqual(_parse_version("4.0"), (4, 0))
        self.assertEqual(_parse_version("v3.2.1"), (3, 2, 1))
        self.assertEqual(_parse_version("4.0.0"), (4, 0, 0))

    def test_parse_rejects_non_numeric(self):
        self.assertIsNone(_parse_version("4.0-beta"))
        self.assertIsNone(_parse_version("unknown"))
        self.assertIsNone(_parse_version(""))

    def test_older_remote_is_not_newer(self):
        # 本地领先远端（开发中的 4.0 对已发布的 3.2）不能报新版本。
        self.assertFalse(_is_remote_newer("3.2", "4.0"))
        self.assertFalse(_is_remote_newer("3.2.9", "4.0"))
        # 带后缀的本地版本无法比较，宁可安静也不误报。
        self.assertIsNone(_is_remote_newer("4.0", "4.0.1-local"))
        self.assertIsNone(_is_remote_newer("4.0", "unknown"))

    def test_newer_remote_is_newer(self):
        self.assertTrue(_is_remote_newer("4.1", "4.0"))
        self.assertTrue(_is_remote_newer("4.0.1", "4.0"))
        self.assertTrue(_is_remote_newer("5", "4.9.9"))

    def test_padding_makes_4_and_4_0_equal(self):
        self.assertFalse(_is_remote_newer("4", "4.0"))
        self.assertFalse(_is_remote_newer("4.0", "4"))

    def test_check_for_updates_is_detection_only(self):
        with (
            patch.object(updater, "_inject_proxy_env") as inject_proxy,
            patch.object(updater, "_check_version_via_api", return_value=True) as check_api,
        ):
            self.assertTrue(updater.check_for_updates())
            self.assertTrue(updater.check_and_update())

        self.assertEqual(inject_proxy.call_count, 2)
        self.assertEqual(check_api.call_count, 2)

    @staticmethod
    def _latest_release_response(version="4.1"):
        response = Mock()
        response.status_code = 302
        response.headers = {
            "Location": (
                "https://github.com/yzr278892/arxiv-daily-researcher/"
                f"releases/tag/v{version}"
            )
        }
        return response

    def test_new_release_is_marked_only_after_a_delivery(self):
        response = self._latest_release_response()
        with (
            patch("requests.get", return_value=response),
            patch.object(updater, "_get_local_version", return_value="4.0"),
            patch.object(updater, "_update_already_notified", return_value=False),
            patch.object(updater, "_send_update_notification", return_value=True) as send,
            patch.object(updater, "_mark_update_notified") as mark,
        ):
            self.assertTrue(updater._check_version_via_api())

        send.assert_called_once()
        mark.assert_called_once_with("4.1")

    def test_new_release_retries_when_all_notifications_fail(self):
        response = self._latest_release_response()
        with (
            patch("requests.get", return_value=response),
            patch.object(updater, "_get_local_version", return_value="4.0"),
            patch.object(updater, "_update_already_notified", return_value=False),
            patch.object(updater, "_send_update_notification", return_value=False) as send,
            patch.object(updater, "_mark_update_notified") as mark,
        ):
            self.assertTrue(updater._check_version_via_api())

        send.assert_called_once()
        mark.assert_not_called()

    def test_release_redirect_avoids_rest_api_rate_limits(self):
        response = self._latest_release_response("4.1")
        with patch("requests.get", return_value=response) as get:
            release = updater._fetch_latest_release(__import__("requests"), Mock())

        self.assertEqual(
            release,
            (
                "4.1",
                "https://github.com/yzr278892/arxiv-daily-researcher/releases/tag/v4.1",
                "请查看发布页面中的完整更新日志。",
            ),
        )
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], updater.GITHUB_LATEST_RELEASE_PAGE)


if __name__ == "__main__":
    unittest.main()
