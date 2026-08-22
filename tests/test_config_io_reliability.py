import json
import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.config_io import (  # noqa: E402
    _atomic_write_text,
    build_config_dict,
    flatten_config_dict,
    read_config_json,
    validate_config_document,
    write_config_json,
    write_env,
)
from config import (  # noqa: E402
    ConfigurationLoadError,
    Settings,
    resolve_project_relative_path,
)


class ConfigIOReliabilityTests(unittest.TestCase):
    def test_existing_invalid_config_fails_closed_without_partial_settings(self):
        """A broken user config must not silently restart with default scope."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                """
                {
                  search_settings: {search_days: 21},
                  target_domains: {domains: 'not-a-list'}
                }
                """,
                encoding="utf-8",
            )
            settings = Settings()
            original_days = settings.SEARCH_DAYS
            original_domains = list(settings.TARGET_DOMAINS)

            with self.assertRaisesRegex(ConfigurationLoadError, "拒绝使用默认配置"):
                settings.load_from_search_config(config_path)

            self.assertEqual(settings.SEARCH_DAYS, original_days)
            self.assertEqual(settings.TARGET_DOMAINS, original_domains)

    def test_non_object_config_root_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(ConfigurationLoadError, "根节点必须是 JSON 对象"):
                Settings().load_from_search_config(config_path)

    def test_runtime_config_rejects_absolute_and_parent_traversal_paths(self):
        for configured_path in ("/tmp/outside.db", "../../outside.db", "data/../outside.db"):
            with self.subTest(configured_path=configured_path), tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / "config.json"
                config_path.write_text(
                    '{daily_research: {db_path: ' + repr(configured_path) + "}}",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ConfigurationLoadError, "项目相对路径|父目录遍历"):
                    Settings().load_from_search_config(config_path)

    def test_runtime_config_honors_history_dir_and_keeps_all_paths_in_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            config_path.write_text(
                """
                {
                  paths: {data_dir: 'runtime-data', history_dir: 'runtime-data/history'},
                  keyword_tracker: {database: {path: 'runtime-data/keywords/store.db'}},
                  daily_research: {db_path: 'runtime-data/daily/custom.db'}
                }
                """,
                encoding="utf-8",
            )
            settings = Settings(PROJECT_ROOT=root)

            settings.load_from_search_config(config_path)

            self.assertEqual(settings.DATA_DIR, root / "runtime-data")
            self.assertEqual(settings.HISTORY_DIR, root / "runtime-data" / "history")
            self.assertEqual(settings.KEYWORD_DB_PATH, root / "runtime-data" / "keywords" / "store.db")
            self.assertEqual(settings.DAILY_RESEARCH_DB_PATH, root / "runtime-data" / "daily" / "custom.db")

    def test_custom_data_dir_moves_implicit_state_paths_as_one_tree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            config_path.write_text("{paths: {data_dir: 'portable-state'}}", encoding="utf-8")
            settings = Settings(PROJECT_ROOT=root)

            settings.load_from_search_config(config_path)

            state_root = root / "portable-state"
            self.assertEqual(settings.DATA_DIR, state_root)
            self.assertEqual(settings.REF_PDF_DIR, state_root / "reference_pdfs")
            self.assertEqual(settings.REPORTS_DIR, state_root / "reports")
            self.assertEqual(settings.RESEARCH_REPORTS_DIR, state_root / "reports" / "trend_research")
            self.assertEqual(settings.DOWNLOAD_DIR, state_root / "downloaded_pdfs")
            self.assertEqual(settings.HISTORY_FILE, state_root / "history.json")
            self.assertEqual(settings.HISTORY_DIR, state_root / "history")
            self.assertEqual(settings.KEYWORD_DB_PATH, state_root / "keywords" / "keywords.db")
            self.assertEqual(
                settings.DAILY_RESEARCH_DB_PATH,
                state_root / "daily_research" / "daily_research.db",
            )

    def test_project_relative_path_resolver_rejects_an_existing_escape_symlink(self):
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as outside_dir:
            root = Path(temp_dir)
            (root / "linked").symlink_to(Path(outside_dir), target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "项目目录内"):
                resolve_project_relative_path(root, "linked/state.db", label="test.path")

    def test_config_io_rejects_unsafe_paths_before_backup_or_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text('{"old": true}\n', encoding="utf-8")
            unsafe = {"daily_research": {"db_path": "../outside.db"}}

            with self.assertRaisesRegex(ValueError, "父目录遍历"):
                write_config_json(unsafe, config_path)

            self.assertEqual(config_path.read_text(encoding="utf-8"), '{"old": true}\n')
            self.assertFalse(config_path.with_suffix(".json.bak").exists())

    def test_config_io_read_and_document_validation_fail_closed_for_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text('{paths: {reports: "/tmp/reports"}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "项目相对路径"):
                read_config_json(config_path)
            with self.assertRaisesRegex(ValueError, "父目录遍历"):
                validate_config_document({"paths": {"reports": "../reports"}})

    def test_atomic_config_write_keeps_previous_content_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text('{"old": true}\n', encoding="utf-8")

            with patch("utils.config_io.os.replace", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    _atomic_write_text(path, '{"new": true}\n')

            self.assertEqual(path.read_text(encoding="utf-8"), '{"old": true}\n')
            self.assertEqual(list(Path(temp_dir).glob(".*.tmp")), [])

    def test_bind_mounted_file_is_rewritten_in_place_when_replace_is_busy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".env"
            path.write_text("OLD=value\n", encoding="utf-8")
            inode_before = path.stat().st_ino
            os.chmod(path, 0o600)

            busy = OSError(errno.EBUSY, "Device or resource busy")
            with patch("utils.config_io.os.replace", side_effect=busy):
                _atomic_write_text(path, "NEW=value\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "NEW=value\n")
            # The mounted inode must survive: renaming it away would unmount
            # the host file inside the container.
            self.assertEqual(path.stat().st_ino, inode_before)
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
            self.assertEqual(list(Path(temp_dir).glob(".*.tmp")), [])

    def test_config_and_env_writes_are_atomic_and_keep_expected_permissions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            env_path = root / ".env"

            write_config_json({"search_settings": {"days": 1}}, config_path)
            with patch("utils.config_io.ENV_EXAMPLE_PATH", root / "does-not-exist"):
                write_env({"SECRET": "value"}, env_path)

            self.assertEqual(oct(config_path.stat().st_mode & 0o777), "0o644")
            self.assertEqual(oct(env_path.stat().st_mode & 0o777), "0o600")
            self.assertIn('"search_settings"', config_path.read_text(encoding="utf-8"))
            self.assertEqual(env_path.read_text(encoding="utf-8"), "SECRET=value\n")

    def test_config_write_preserves_an_existing_custom_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text('{"old": true}\n', encoding="utf-8")
            config_path.chmod(0o640)

            write_config_json({"search_settings": {"days": 2}}, config_path)

            self.assertEqual(oct(config_path.stat().st_mode & 0o777), "0o640")

    def test_legacy_daily_result_caps_are_not_written_or_exposed(self):
        """Legacy fetch caps stay ignored; the new limit is downstream only."""
        config = build_config_dict(
            search_days=3,
            max_results=1,
            max_results_per_source={"arxiv": 1},
            daily_max_papers_per_run=5,
        )
        self.assertEqual(config["search_settings"], {"search_days": 3})
        self.assertEqual(config["daily_research"]["max_papers_per_run"], 5)

        legacy_flat = flatten_config_dict(
            {
                "search_settings": {
                    "search_days": 3,
                    "max_results": 1,
                    "max_results_per_source": {"arxiv": 1},
                }
            }
        )
        self.assertEqual(legacy_flat["search_days"], 3)
        self.assertNotIn("max_results", legacy_flat)
        self.assertNotIn("max_results_per_source", legacy_flat)
        self.assertEqual(legacy_flat["daily_max_papers_per_run"], 200)

    def test_daily_queue_limit_round_trips_and_sqlite_is_mandatory(self):
        config = build_config_dict(
            daily_max_papers_per_run=10,
            daily_research_persistence_enabled=False,
        )
        self.assertEqual(config["daily_research"]["max_papers_per_run"], 10)
        self.assertNotIn("persistence_enabled", config["daily_research"])

        flat = flatten_config_dict(
            {
                "daily_research": {
                    "max_papers_per_run": 7,
                    "persistence_enabled": False,
                }
            }
        )
        self.assertEqual(flat["daily_max_papers_per_run"], 7)
        self.assertTrue(flat["daily_research_persistence_enabled"])

        for invalid in (-1, True, "5"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "非负整数"):
                    build_config_dict(daily_max_papers_per_run=invalid)
                with self.assertRaisesRegex(ValueError, "非负整数"):
                    validate_config_document(
                        {"daily_research": {"max_papers_per_run": invalid}}
                    )

    def test_runtime_rejects_negative_daily_queue_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text(
                "{daily_research: {max_papers_per_run: -1}}", encoding="utf-8"
            )
            with self.assertRaisesRegex(ConfigurationLoadError, "非负整数"):
                Settings().load_from_search_config(path)

    def test_explicit_empty_source_or_domain_lists_are_not_replaced_by_defaults(self):
        config = build_config_dict(enabled_sources=[], domains=[])
        self.assertEqual(config["data_sources"]["enabled"], [])
        self.assertEqual(config["target_domains"]["domains"], [])

        flat = flatten_config_dict(config)
        self.assertEqual(flat["enabled_sources"], [])
        self.assertEqual(flat["domains"], [])

    def test_arxiv_announcement_grace_round_trips_through_config_io(self):
        config = build_config_dict(arxiv_announcement_lookback_grace_days=4)
        self.assertEqual(
            config["data_sources"]["arxiv"]["announcement_lookback_grace_days"], 4
        )

        flat = flatten_config_dict(config)
        self.assertEqual(flat["arxiv_announcement_lookback_grace_days"], 4)

    def test_pdf_download_limit_round_trips_through_config_io(self):
        config = build_config_dict(pdf_download_max_bytes=12 * 1024 * 1024)
        self.assertEqual(config["pdf_parser"]["download_max_bytes"], 12 * 1024 * 1024)
        flat = flatten_config_dict(config)
        self.assertEqual(flat["pdf_download_max_bytes"], 12 * 1024 * 1024)

    def test_huggingface_papers_configuration_and_proxy_round_trip(self):
        config = build_config_dict(
            enabled_sources=["arxiv", "huggingface_papers"],
            huggingface_papers_availability_lag_days=3,
            huggingface_papers_lookback_grace_days=4,
            huggingface_papers_request_timeout_seconds=45,
            huggingface_papers_request_interval_seconds=0.5,
            proxy_huggingface_papers=True,
        )
        hf = config["data_sources"]["huggingface_papers"]
        self.assertEqual(hf["availability_lag_days"], 3)
        self.assertEqual(hf["lookback_grace_days"], 4)
        self.assertEqual(hf["request_timeout_seconds"], 45)
        self.assertEqual(hf["request_interval_seconds"], 0.5)
        self.assertTrue(config["proxy"]["scope"]["huggingface_papers"])

        flat = flatten_config_dict(config)
        self.assertEqual(flat["enabled_sources"], ["arxiv", "huggingface_papers"])
        self.assertEqual(flat["huggingface_papers_availability_lag_days"], 3)
        self.assertEqual(flat["huggingface_papers_lookback_grace_days"], 4)
        self.assertEqual(flat["huggingface_papers_request_timeout_seconds"], 45)
        self.assertEqual(flat["huggingface_papers_request_interval_seconds"], 0.5)
        self.assertTrue(flat["proxy_huggingface_papers"])

    def test_declarative_extra_sources_round_trip_without_executable_fields(self):
        definitions = [
            {
                "type": "openalex_journal",
                "code": "custom_physics",
                "display_name": "Custom Phys.",
                "full_name": "Custom Physics Journal",
                "issn": ["1234-567X"],
            }
        ]
        config = build_config_dict(
            enabled_sources=["arxiv", "prl"],
            extra_sources_enabled=True,
            extra_source_definitions=definitions,
        )

        self.assertEqual(
            config["data_sources"]["enabled"],
            ["arxiv", "prl", "custom_physics"],
        )
        self.assertEqual(
            config["data_sources"]["extra_sources"],
            {"enabled": True, "definitions": definitions},
        )
        flat = flatten_config_dict(config)
        self.assertTrue(flat["extra_sources_enabled"])
        self.assertEqual(flat["extra_source_definitions"], definitions)

        unsafe = [
            {
                **definitions[0],
                "python": "__import__('os').system('id')",
            }
        ]
        with self.assertRaisesRegex(ValueError, "不支持字段"):
            validate_config_document(
                {
                    "data_sources": {
                        "extra_sources": {"enabled": True, "definitions": unsafe}
                    }
                }
            )

    def test_disabled_extra_source_definitions_are_retained_but_not_enabled(self):
        definitions = [
            {
                "type": "openalex_journal",
                "code": "custom_physics",
                "display_name": "Custom Phys.",
                "full_name": "Custom Physics Journal",
                "issn": ["1234-567X"],
            }
        ]
        config = build_config_dict(
            enabled_sources=["arxiv"],
            extra_sources_enabled=False,
            extra_source_definitions=definitions,
        )

        self.assertEqual(config["data_sources"]["enabled"], ["arxiv"])
        self.assertEqual(
            config["data_sources"]["extra_sources"]["definitions"], definitions
        )

    def test_explicit_extra_source_switch_cannot_be_bypassed_by_stale_codes(self):
        definitions = [
            {
                "type": "openalex_journal",
                "code": "custom_physics",
                "display_name": "Custom Phys.",
                "full_name": "Custom Physics Journal",
                "issn": ["1234-567X"],
            }
        ]
        document = {
            "data_sources": {
                "enabled": ["arxiv", "custom_physics"],
                "journals": ["pra"],
                "extra_sources": {
                    "enabled": False,
                    "definitions": definitions,
                },
            }
        }

        flat = flatten_config_dict(document)
        self.assertFalse(flat["extra_sources_enabled"])
        self.assertEqual(flat["enabled_sources"], ["arxiv"])
        self.assertEqual(flat["extra_source_definitions"], definitions)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            runtime = Settings()
            runtime.load_from_search_config(path)

        self.assertEqual(runtime.ENABLED_SOURCES, ["arxiv"])
        self.assertEqual(runtime.TARGET_JOURNALS, [])
        self.assertFalse(runtime.EXTRA_SOURCES_ENABLED)

    def test_extra_source_definition_order_is_stable_at_runtime(self):
        definitions = [
            {
                "type": "openalex_journal",
                "code": code,
                "display_name": code.upper(),
                "full_name": f"{code.upper()} Journal",
                "issn": [issn],
            }
            for code, issn in (("source_b", "1234-567X"), ("source_a", "2345-678X"))
        ]
        document = {
            "data_sources": {
                "enabled": ["prl"],
                "extra_sources": {"enabled": True, "definitions": definitions},
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            runtime = Settings()
            runtime.load_from_search_config(path)

        self.assertEqual(runtime.ENABLED_SOURCES, ["prl", "source_b", "source_a"])

    def test_webdav_proxy_scope_round_trips_independently(self):
        config = build_config_dict(proxy_webdav=True, proxy_notifications=False)
        self.assertTrue(config["proxy"]["scope"]["webdav"])
        self.assertFalse(config["proxy"]["scope"]["notifications"])

        flat = flatten_config_dict(config)
        self.assertTrue(flat["proxy_webdav"])
        self.assertFalse(flat["proxy_notifications"])

        # An old config without the new key must keep the former behavior:
        # global proxying also covered WebDAV.
        self.assertTrue(flatten_config_dict({"proxy": {"scope": {}}})["proxy_webdav"])

    def test_legacy_configuration_without_hf_block_stays_compatible(self):
        flat = flatten_config_dict({"data_sources": {"enabled": ["arxiv"]}})
        self.assertEqual(flat["huggingface_papers_availability_lag_days"], 2)
        self.assertEqual(flat["huggingface_papers_lookback_grace_days"], 2)
        self.assertFalse(flat["proxy_huggingface_papers"])

    def test_v2_scoring_strategy_round_trips_and_missing_strategy_is_legacy(self):
        config = build_config_dict(
            score_strategy="core_relevance_v2",
            core_relevance_threshold=6.5,
            core_keyword_min_score=8.0,
            reference_ranking_weight=0.4,
        )
        self.assertEqual(config["scoring_settings"]["strategy"]["id"], "core_relevance_v2")
        flat = flatten_config_dict(config)
        self.assertEqual(flat["score_strategy"], "core_relevance_v2")
        self.assertEqual(flat["core_relevance_threshold"], 6.5)
        self.assertEqual(flat["core_keyword_min_score"], 8.0)
        self.assertEqual(flat["reference_ranking_weight"], 0.4)

        legacy = flatten_config_dict({"scoring_settings": {}})
        self.assertEqual(legacy["score_strategy"], "legacy_weighted_keyword_v1")
        self.assertFalse(legacy["score_strategy_explicit"])
        legacy_round_trip = build_config_dict(**legacy)
        self.assertNotIn("strategy", legacy_round_trip["scoring_settings"])


if __name__ == "__main__":
    unittest.main()


class ConfigCommentPreservationTests(unittest.TestCase):
    def _write_with_comments(self, root: Path):
        path = root / "config.json"
        write_config_json({"search_settings": {"search_days": 14}, "daily_research": {"max_papers_per_run": 0}}, path)
        hand_commented = path.read_text(encoding="utf-8").replace(
            '    "search_days": 14',
            "    // Hand-written: full window is scanned.\n    \"search_days\": 14",
        ).replace(
            '    "max_papers_per_run": 0',
            "    // 0 = all pending; positive caps this run only.\n    \"max_papers_per_run\": 3",
        )
        path.write_text(hand_commented, encoding="utf-8")
        return path

    def test_hand_comments_survive_a_rewrite_with_changed_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = self._write_with_comments(root)
            config = read_config_json(path)
            config["daily_research"]["max_papers_per_run"] = 5
            write_config_json(config, path)

            text = path.read_text(encoding="utf-8")
            self.assertIn("// Hand-written: full window is scanned.", text)
            self.assertIn("// 0 = all pending; positive caps this run only.", text)
            self.assertIn('"max_papers_per_run": 5', text)
            self.assertEqual(read_config_json(path)["daily_research"]["max_papers_per_run"], 5)

    def test_generated_section_headers_are_not_duplicated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = self._write_with_comments(root)
            before = path.read_text(encoding="utf-8")
            header_count_before = before.count("// ==")
            config = read_config_json(path)
            write_config_json(config, path)

            text = path.read_text(encoding="utf-8")
            # 生成的分节横幅只会来自 writer 自己；回注不得把它们翻倍。
            self.assertEqual(text.count("// =="), header_count_before)

    def test_comment_for_removed_key_is_dropped_without_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = self._write_with_comments(root)
            config = read_config_json(path)
            del config["daily_research"]["max_papers_per_run"]
            write_config_json(config, path)

            text = path.read_text(encoding="utf-8")
            self.assertNotIn("0 = all pending", text)
            self.assertIn("// Hand-written: full window is scanned.", text)

    def test_repeated_member_names_anchor_in_document_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "config.json"
            path.write_text(
                '{\n'
                '  "a": {\n'
                '    // comment for first enabled\n'
                '    "enabled": true\n'
                '  },\n'
                '  "b": {\n'
                '    // comment for second enabled\n'
                '    "enabled": false\n'
                '  }\n'
                '}\n',
                encoding="utf-8",
            )
            config = read_config_json(path)
            config["a"]["enabled"] = False
            write_config_json(config, path)

            text = path.read_text(encoding="utf-8")
            first = text.index("comment for first enabled")
            second = text.index("comment for second enabled")
            self.assertLess(first, second)
            self.assertIn("comment for first enabled\n    \"enabled\": false", text)


class LLMConnectionTestTests(unittest.TestCase):
    """LLM 连通性测试必须能跑在 WebUI 镜像里（无 worker 的 config/llm_request_pool 栈）。"""

    def _run_with_fake_openai(self, raise_exc=None):
        created = {}

        class _Completions:
            def create(self, **kwargs):
                if raise_exc is not None:
                    raise raise_exc
                created["kwargs"] = kwargs
                return type("Resp", (), {"model": "stub-model"})()

        class _Client:
            def __init__(self, api_key, base_url, timeout):
                self.chat = type("Chat", (), {"completions": _Completions()})()

        import types

        module = types.ModuleType("openai")
        module.OpenAI = _Client
        with patch.dict(sys.modules, {"openai": module}):
            from utils.config_io import validate_llm_connection

            result = validate_llm_connection("k", "https://api.example/v1", "m")
        return result, created

    def test_success_uses_client_directly_without_worker_pool(self):
        result, created = self._run_with_fake_openai()
        self.assertEqual(result[0], True)
        self.assertIn("stub-model", result[1])
        self.assertEqual(created["kwargs"]["model"], "m")

    def test_api_error_is_reported_as_failure(self):
        result, _ = self._run_with_fake_openai(raise_exc=RuntimeError("boom"))
        self.assertEqual(result[0], False)
        self.assertIn("boom", result[1])
