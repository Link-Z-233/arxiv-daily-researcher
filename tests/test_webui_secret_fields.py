"""Regression tests for safe local-WebUI secret handling."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui import secret_fields  # noqa: E402
from webui.tabs import data_management, llm, notifications  # noqa: E402


class _FakeStreamlit:
    def __init__(self, session_state):
        self.session_state = session_state
        self.text_input_calls = []
        self.caption_calls = []
        self.checkbox_calls = []

    def text_input(self, *args, **kwargs):
        self.text_input_calls.append((args, kwargs))
        return self.session_state.get(kwargs["key"], "")

    def caption(self, value):
        self.caption_calls.append(value)

    def checkbox(self, *args, **kwargs):
        self.checkbox_calls.append((args, kwargs))
        return self.session_state.get(kwargs["key"], False)


class WebUISecretFieldTests(unittest.TestCase):
    def test_saved_secret_is_never_used_as_a_browser_widget_value(self):
        state = {}
        fake_st = _FakeStreamlit(state)
        saved_secret = "server-only-secret"

        entered = secret_fields.render_secret_input(
            fake_st,
            label="API key",
            env_values={"API_KEY": saved_secret},
            env_key="API_KEY",
            field_key="api_key",
            configured_hint="configured",
            clear_label="clear",
        )

        self.assertEqual(entered, "")
        self.assertEqual(fake_st.text_input_calls[0][1]["value"], "")
        self.assertNotIn(saved_secret, fake_st.text_input_calls[0][1].values())
        self.assertEqual(fake_st.caption_calls, ["configured"])
        self.assertEqual(fake_st.checkbox_calls[0][1]["key"], "api_key__clear_saved_secret")

    def test_initialization_only_erases_legacy_session_state_once(self):
        state = {"api_key": "legacy-browser-copy"}

        secret_fields.initialize_secret_field_state(state, "api_key")
        self.assertEqual(state["api_key"], "")

        state["api_key"] = "newly-entered-secret"
        secret_fields.initialize_secret_field_state(state, "api_key")
        self.assertEqual(state["api_key"], "newly-entered-secret")

    def test_resolver_preserves_replaces_and_explicitly_clears_secret(self):
        saved = {"API_KEY": "persisted-secret"}

        self.assertEqual(
            secret_fields.resolve_secret_value(saved, "API_KEY", "api_key", {}),
            "persisted-secret",
        )
        self.assertEqual(
            secret_fields.resolve_secret_value(
                saved, "API_KEY", "api_key", {"api_key": "replacement"}
            ),
            "replacement",
        )
        self.assertEqual(
            secret_fields.resolve_secret_value(
                saved,
                "API_KEY",
                "api_key",
                {"api_key": "", "api_key__clear_saved_secret": True},
            ),
            "",
        )

    def test_collectors_keep_saved_secret_when_widget_is_blank(self):
        env_values = {
            "CHEAP_LLM__API_KEY": "cheap-saved",
            "SMART_LLM__API_KEY": "smart-saved",
            "MINERU_API_KEY": "mineru-saved",
            "SEMANTIC_SCHOLAR_API_KEY": "s2-saved",
            "OPENALEX_API_KEY": "openalex-saved",
            "SMTP_PASSWORD": "smtp-saved",
            "WECHAT_WEBHOOK_URL": "wechat-saved",
            "DINGTALK_WEBHOOK_URL": "dingtalk-saved",
            "DINGTALK_SECRET": "dingtalk-secret",
            "TELEGRAM_BOT_TOKEN": "telegram-saved",
            "SLACK_WEBHOOK_URL": "slack-saved",
            "GENERIC_WEBHOOK_URL": "generic-saved",
            "WEBDAV_PASSWORD": "webdav-saved",
        }
        state = {}
        fake_st = SimpleNamespace(session_state=state)

        with (
            patch.object(llm, "st", fake_st),
            patch.object(notifications, "st", fake_st),
            patch.object(data_management, "st", fake_st),
        ):
            llm_updates = llm.collect(env_values, {})
            notification_updates, _ = notifications.collect(env_values, {})
            data_updates, _ = data_management.collect(env_values, {})

        self.assertEqual(llm_updates["CHEAP_LLM__API_KEY"], "cheap-saved")
        self.assertEqual(llm_updates["SMART_LLM__API_KEY"], "smart-saved")
        self.assertEqual(llm_updates["MINERU_API_KEY"], "mineru-saved")
        self.assertEqual(notification_updates["SMTP_PASSWORD"], "smtp-saved")
        self.assertEqual(notification_updates["TELEGRAM_BOT_TOKEN"], "telegram-saved")
        self.assertEqual(data_updates["WEBDAV_PASSWORD"], "webdav-saved")

    def test_collectors_honor_explicit_secret_clear(self):
        env_values = {
            "CHEAP_LLM__API_KEY": "cheap-saved",
            "SMTP_PASSWORD": "smtp-saved",
            "WEBDAV_PASSWORD": "webdav-saved",
        }
        state = {
            "cheap_api_key__clear_saved_secret": True,
            "smtp_password__clear_saved_secret": True,
            "webdav_password__clear_saved_secret": True,
        }
        fake_st = SimpleNamespace(session_state=state)

        with (
            patch.object(llm, "st", fake_st),
            patch.object(notifications, "st", fake_st),
            patch.object(data_management, "st", fake_st),
        ):
            llm_updates = llm.collect(env_values, {})
            notification_updates, _ = notifications.collect(env_values, {})
            data_updates, _ = data_management.collect(env_values, {})

        self.assertEqual(llm_updates["CHEAP_LLM__API_KEY"], "")
        self.assertEqual(notification_updates["SMTP_PASSWORD"], "")
        self.assertEqual(data_updates["WEBDAV_PASSWORD"], "")

    def test_successful_save_cleanup_forgets_entered_secret_but_not_marker(self):
        state = {
            "api_key": "just-entered",
            "api_key__clear_saved_secret": True,
            "api_key__secret_widget_initialized": True,
        }
        secret_fields.clear_secret_field_state(state, ["api_key"])
        self.assertEqual(state["api_key"], "")
        self.assertFalse(state["api_key__clear_saved_secret"])
        self.assertTrue(state["api_key__secret_widget_initialized"])


if __name__ == "__main__":
    unittest.main()
