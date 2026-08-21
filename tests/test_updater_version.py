import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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


if __name__ == "__main__":
    unittest.main()
