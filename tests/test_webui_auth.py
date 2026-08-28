import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webui.auth import (  # noqa: E402
    _disabled_auth_values,
    WebUIAuthConfig,
    create_persistent_session_token,
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
        self.assertEqual(default.session_timeout_minutes, 480)

        invalid_timeout = read_auth_config({"WEBUI_SESSION_TIMEOUT_MINUTES": "0"})
        self.assertEqual(invalid_timeout.session_timeout_minutes, 480)

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


if __name__ == "__main__":
    unittest.main()
