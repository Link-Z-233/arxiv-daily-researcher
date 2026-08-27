import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _FakeSessionState(dict):
    """Minimal session_state stand-in: `.get` over a plain dict."""

    def get(self, key, default=None):
        return super().get(key, default)


def _patch_session(state):
    """Patch st.session_state on every webui tab module at once."""
    import contextlib

    from webui.tabs import (
        advanced,
        data_management,
        keywords,
        llm,
        notifications,
        run_manager,
        scoring,
        search,
        trend_runner,
    )

    modules = [
        advanced,
        data_management,
        keywords,
        llm,
        notifications,
        run_manager,
        scoring,
        search,
        trend_runner,
    ]
    stack = contextlib.ExitStack()
    for module in modules:
        stack.enter_context(patch.object(module.st, "session_state", state))
    return stack


class LazyPageCollectTests(unittest.TestCase):
    """按页渲染后，未访问页面的 collect() 必须保留磁盘现值。

    侧边栏导航每次只渲染一个页面；从未打开过的页面没有任何会话状态。
    如果 collect() 回退到硬编码默认值，一次保存会静默改写所有未浏览
    页面的设置（曾实际发生在评分公式上）。
    """

    def _sample_flat_config(self):
        return {
            "enable_html_report": False,
            "enable_markdown_report": False,
            "include_all_in_report": False,
            "daily_max_papers_per_run": 7,
            "primary_keywords": ["superconductivity", "cold atoms"],
            "primary_keyword_weight": 2.0,
            "similarity_threshold": 0.9,
            "enabled_sources": ["arxiv", "prl"],
            "domains": ["quant-ph", "cond-mat.stat-mech"],
            "extra_sources_enabled": True,
            "extra_source_definitions": [],
            "notifications_enabled": True,
            "notify_wechat_enabled": True,
            "notification_top_n": 3,
            "webdav_enabled": True,
            "webdav_cron_schedule": "30 22 * * *",
            "backup_local_retention_days": 14,
            "backup_local_same_day_max_count": 6,
            "pdf_parser_mode": "pymupdf",
            "concurrency_workers": 6,
            "trend_max_results": 250,
            "trend_sort_order": "descending",
            "trend_enabled_skills": ["comprehensive_analysis"],
            "trend_output_formats": ["markdown"],
            "pdf_download_max_bytes": 80 * 1024 * 1024,
            "passing_score_base": 1.5,
            "passing_score_weight_coefficient": 2.5,
        }

    def test_unvisited_pages_keep_configured_values_on_save(self):
        from webui.tabs import (
            advanced,
            data_management,
            keywords,
            notifications,
            run_manager,
            search,
            trend_runner,
        )

        flat = self._sample_flat_config()
        with _patch_session(_FakeSessionState()):
            rm = run_manager.collect({}, flat)
            kw = keywords.collect({}, flat)
            se = search.collect({}, flat)
            nf_env, nf_cfg = notifications.collect({}, flat)
            adv = advanced.collect({}, flat)
            _, dm = data_management.collect({}, flat)
            tr = trend_runner.collect({}, flat)

        self.assertEqual(rm["daily_max_papers_per_run"], 7)
        self.assertFalse(rm["enable_html_report"])
        self.assertEqual(kw["primary_keywords"], ["superconductivity", "cold atoms"])
        self.assertEqual(kw["primary_keyword_weight"], 2.0)
        self.assertEqual(se["enabled_sources"], ["arxiv", "prl"])
        self.assertEqual(se["domains"], ["quant-ph", "cond-mat.stat-mech"])
        self.assertTrue(se["extra_sources_enabled"])
        self.assertTrue(nf_cfg["notifications_enabled"])
        self.assertTrue(nf_cfg["notify_wechat_enabled"])
        self.assertEqual(nf_cfg["notification_top_n"], 3)
        self.assertEqual(adv["pdf_download_max_bytes"], 80 * 1024 * 1024)
        self.assertEqual(adv["concurrency_workers"], 6)
        self.assertEqual(adv["pdf_parser_mode"], "pymupdf")
        self.assertEqual(dm["backup_local_retention_days"], 14)
        self.assertEqual(dm["backup_local_same_day_max_count"], 6)
        self.assertEqual(tr["trend_max_results"], 250)
        self.assertEqual(tr["trend_sort_order"], "descending")
        self.assertEqual(tr["trend_enabled_skills"], ["comprehensive_analysis"])
        self.assertEqual(tr["trend_output_formats"], ["markdown"])

    def test_unvisited_env_pages_keep_dotenv_values(self):
        from webui.tabs import data_management, llm, notifications

        env_values = {
            "CHEAP_LLM__BASE_URL": "https://example.test/v1",
            "CHEAP_LLM__MODEL_NAME": "test-model",
            "SMTP_HOST": "smtp.example.test",
            "SMTP_TO": "me@example.test",
            "WEBDAV_URL": "https://dav.example.test",
        }
        with _patch_session(_FakeSessionState()):
            llm_env = llm.collect(env_values, {})
            nf_env, _ = notifications.collect(env_values, {})
            dm_env, _ = data_management.collect(env_values, {})

        self.assertEqual(llm_env["CHEAP_LLM__BASE_URL"], "https://example.test/v1")
        self.assertEqual(llm_env["CHEAP_LLM__MODEL_NAME"], "test-model")
        self.assertEqual(nf_env["SMTP_HOST"], "smtp.example.test")
        self.assertEqual(nf_env["SMTP_TO"], "me@example.test")
        self.assertEqual(dm_env["WEBDAV_URL"], "https://dav.example.test")

    def test_visited_widget_state_still_wins(self):
        from webui.tabs import run_manager

        session = _FakeSessionState({"daily_max_papers_per_run": 3})
        with _patch_session(session):
            updates = run_manager.collect({}, {"daily_max_papers_per_run": 7})
        self.assertEqual(updates["daily_max_papers_per_run"], 3)

    def test_mineru_panel_follows_the_current_pdf_parser_selection(self):
        from webui.tabs import llm

        with _patch_session(_FakeSessionState()):
            self.assertFalse(llm._mineru_parser_is_selected({"pdf_parser_mode": "pymupdf"}))
            self.assertTrue(llm._mineru_parser_is_selected({"pdf_parser_mode": "mineru"}))

        with _patch_session(_FakeSessionState({"pdf_parser_mode": "mineru"})):
            self.assertTrue(llm._mineru_parser_is_selected({"pdf_parser_mode": "pymupdf"}))

    def test_backup_now_uses_current_retention_value_including_zero(self):
        """An immediate backup must honor the unsaved value currently in the UI."""
        from webui.tabs import data_management

        class _Spinner:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class _FakeStreamlit:
            def __init__(self):
                self.session_state = _FakeSessionState(
                    {"backup_local_retention_days": 0}
                )
                self.reruns = 0

            def info(self, *_args, **_kwargs):
                pass

            def success(self, *_args, **_kwargs):
                pass

            def spinner(self, *_args, **_kwargs):
                return _Spinner()

            def rerun(self):
                self.reruns += 1

        fake_st = _FakeStreamlit()
        with (
            patch.object(data_management, "st", fake_st),
            patch(
                "utils.backup.create_backup",
                return_value={"created": True, "uploaded": False},
            ) as create_backup,
        ):
            data_management._do_backup({}, {})

        create_backup.assert_called_once()
        args, kwargs = create_backup.call_args
        self.assertEqual(args, (data_management._PROJECT_ROOT / "data",))
        self.assertEqual(
            kwargs["database"],
            data_management._PROJECT_ROOT
            / "data"
            / "daily_research"
            / "daily_research.db",
        )
        self.assertEqual(kwargs["retention_days"], 0)
        self.assertEqual(kwargs["same_day_max_count"], 0)
        self.assertIsNone(kwargs["webdav_sync"])
        self.assertEqual(fake_st.reruns, 1)

    def test_backup_now_uses_configured_data_and_database_paths(self):
        """Immediate backups must target the same paths used by the worker."""
        from webui.tabs import data_management

        class _Spinner:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class _FakeStreamlit:
            def __init__(self):
                self.session_state = _FakeSessionState(
                    {"backup_local_retention_days": 7}
                )

            def info(self, *_args, **_kwargs):
                pass

            def success(self, *_args, **_kwargs):
                pass

            def spinner(self, *_args, **_kwargs):
                return _Spinner()

            def rerun(self):
                pass

        fake_st = _FakeStreamlit()
        config = {
            "data_dir": "data/custom_backup_root",
            "daily_research_db_path": "data/custom_sqlite/papers.db",
        }
        with (
            patch.object(data_management, "st", fake_st),
            patch(
                "utils.backup.create_backup",
                return_value={"created": True, "uploaded": False},
            ) as create_backup,
        ):
            data_management._do_backup({}, config)

        args, kwargs = create_backup.call_args
        self.assertEqual(
            args, (data_management._PROJECT_ROOT / "data" / "custom_backup_root",)
        )
        self.assertEqual(
            kwargs["database"],
            data_management._PROJECT_ROOT / "data" / "custom_sqlite" / "papers.db",
        )

    def test_backup_now_does_not_use_webdav_credentials_when_disabled(self):
        """Disabled WebDAV must keep a manual backup strictly local."""
        from webui.tabs import data_management

        class _Spinner:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class _FakeStreamlit:
            def __init__(self):
                self.session_state = _FakeSessionState(
                    {"backup_local_retention_days": 7, "webdav_enabled": False}
                )

            def info(self, *_args, **_kwargs):
                pass

            def success(self, *_args, **_kwargs):
                pass

            def spinner(self, *_args, **_kwargs):
                return _Spinner()

            def rerun(self):
                pass

        fake_st = _FakeStreamlit()
        with (
            patch.object(data_management, "st", fake_st),
            patch(
                "utils.backup.create_backup",
                return_value={"created": True, "uploaded": False},
            ) as create_backup,
            patch(
                "utils.webdav_sync.WebDAVSync",
                side_effect=AssertionError("disabled WebDAV must not be initialized"),
            ),
        ):
            data_management._do_backup(
                {
                    "WEBDAV_URL": "https://dav.example.test/",
                    "WEBDAV_USERNAME": "configured-user",
                    "WEBDAV_PASSWORD": "configured-password",
                },
                {"webdav_enabled": False},
            )

        self.assertIsNone(create_backup.call_args.kwargs["webdav_sync"])


if __name__ == "__main__":
    unittest.main()
