import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui import auth  # noqa: E402
from webui.auth import (  # noqa: E402
    _disabled_auth_values,
    WebUIAuthConfig,
    accounts_for_config,
    change_own_password,
    create_managed_account,
    create_persistent_session_token,
    find_account,
    hash_password,
    read_auth_config,
    validate_password,
    validate_username,
    verify_persistent_session_token,
    verify_password_hash,
)


class WebUIAuthTests(unittest.TestCase):
    def test_password_hash_is_salted_and_verifiable(self):
        first = hash_password("a long administrator passphrase")
        second = hash_password("a long administrator passphrase")

        self.assertNotEqual(first, second)
        self.assertNotIn("a long administrator passphrase", first)
        self.assertNotIn("$", first)
        self.assertTrue(verify_password_hash(first, "a long administrator passphrase"))
        self.assertFalse(verify_password_hash(first, "incorrect passphrase"))

    def test_invalid_password_records_fail_closed(self):
        self.assertIsNone(verify_password_hash("not-a-record", "anything"))
        self.assertIsNone(
            verify_password_hash("pbkdf2_sha256:1:YWJjZA==:YWJjZA==", "anything")
        )

    def test_defaults_enable_auth_and_bound_session_timeout(self):
        default = read_auth_config({})
        self.assertTrue(default.enabled)
        self.assertEqual(default.session_timeout_minutes, 10_080)

        invalid_timeout = read_auth_config({"WEBUI_SESSION_TIMEOUT_MINUTES": "0"})
        self.assertEqual(invalid_timeout.session_timeout_minutes, 10_080)

    def test_trusted_lan_skip_disables_auth_and_clears_admin_record(self):
        values = _disabled_auth_values(
            {
                "WEBUI_AUTH_ENABLED": "true",
                "WEBUI_ADMIN_USERNAME": "admin",
                "WEBUI_ADMIN_PASSWORD_HASH": "pbkdf2_sha256:example",
            }
        )

        self.assertFalse(read_auth_config(values).enabled)
        self.assertEqual(values["WEBUI_ADMIN_USERNAME"], "")
        self.assertEqual(values["WEBUI_ADMIN_PASSWORD_HASH"], "")
        self.assertEqual(values["WEBUI_ACCOUNTS"], "")

    def test_username_and_password_validation(self):
        self.assertIsNone(validate_username("admin.user-1"))
        self.assertIsNotNone(validate_username("ab"))
        self.assertIsNotNone(validate_username("admin name"))
        self.assertIsNotNone(validate_password("short"))
        self.assertIsNone(validate_password("secret"))

    def test_persistent_session_token_survives_refresh_and_expires(self):
        config = WebUIAuthConfig(
            enabled=True,
            username="admin",
            password_hash=hash_password("secret"),
            session_timeout_minutes=15,
        )
        token = create_persistent_session_token(config, now=1_000)

        self.assertTrue(verify_persistent_session_token(config, token, now=1_899))
        self.assertFalse(verify_persistent_session_token(config, token, now=1_900))
        self.assertFalse(
            verify_persistent_session_token(
                WebUIAuthConfig(
                    enabled=True,
                    username="admin",
                    password_hash=hash_password("secret"),
                    session_timeout_minutes=15,
                ),
                token,
                now=1_100,
            )
        )

    def test_account_registry_keeps_legacy_owner_and_supports_secondary_login(self):
        env_values = {
            "WEBUI_AUTH_ENABLED": "true",
            "WEBUI_ADMIN_USERNAME": "owner",
            "WEBUI_ADMIN_PASSWORD_HASH": hash_password("secret"),
        }
        with (
            patch.object(auth, "write_env") as write_env,
            patch.object(auth.st.cache_data, "clear"),
        ):
            create_managed_account(
                env_values,
                actor_username="owner",
                username="operator",
                password="operator-secret",
            )

        saved = write_env.call_args.args[0]
        config = read_auth_config(saved)
        accounts = accounts_for_config(config)
        self.assertEqual([account.username for account in accounts], ["owner", "operator"])
        self.assertTrue(accounts[0].is_owner)
        self.assertFalse(accounts[1].is_owner)
        self.assertEqual(saved["WEBUI_ADMIN_USERNAME"], "owner")
        self.assertIsNotNone(find_account(config, "operator"))
        token = create_persistent_session_token(config, username="operator", now=1_000)
        self.assertTrue(verify_persistent_session_token(config, token, now=1_100))

    def test_password_change_invalidates_only_that_account_session(self):
        env_values = {
            "WEBUI_AUTH_ENABLED": "true",
            "WEBUI_ADMIN_USERNAME": "owner",
            "WEBUI_ADMIN_PASSWORD_HASH": hash_password("secret"),
        }
        with (
            patch.object(auth, "write_env") as write_env,
            patch.object(auth.st.cache_data, "clear"),
        ):
            create_managed_account(
                env_values,
                actor_username="owner",
                username="operator",
                password="operator-secret",
            )
            initial = write_env.call_args.args[0]
            initial_config = read_auth_config(initial)
            owner_token = create_persistent_session_token(
                initial_config, username="owner", now=1_000
            )
            operator_token = create_persistent_session_token(
                initial_config, username="operator", now=1_000
            )
            change_own_password(
                initial,
                username="operator",
                current_password="operator-secret",
                new_password="new-operator-secret",
            )

        updated_config = read_auth_config(write_env.call_args.args[0])
        self.assertTrue(verify_persistent_session_token(updated_config, owner_token, now=1_100))
        self.assertFalse(
            verify_persistent_session_token(updated_config, operator_token, now=1_100)
        )

    def test_secondary_account_cannot_manage_the_registry(self):
        env_values = {
            "WEBUI_AUTH_ENABLED": "true",
            "WEBUI_ADMIN_USERNAME": "owner",
            "WEBUI_ADMIN_PASSWORD_HASH": hash_password("secret"),
        }
        with (
            patch.object(auth, "write_env") as write_env,
            patch.object(auth.st.cache_data, "clear"),
        ):
            create_managed_account(
                env_values,
                actor_username="owner",
                username="operator",
                password="operator-secret",
            )
            with self.assertRaisesRegex(ValueError, "auth_account_owner_required"):
                create_managed_account(
                    write_env.call_args.args[0],
                    actor_username="operator",
                    username="another-user",
                    password="another-secret",
                )


if __name__ == "__main__":
    unittest.main()
