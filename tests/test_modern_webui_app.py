"""HTTP-level regression tests for the standalone modern WebUI.

These tests intentionally keep all account state in memory.  They verify the
presentation layer's authentication boundary without reading or changing a
developer's real ``.env`` file.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from modern_webui import app as modern_app


class ModernWebUIAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env: dict[str, str] = {"WEBUI_AUTH_ENABLED": "true"}
        self.client = TestClient(modern_app.app)
        self.read_env = patch.object(
            modern_app, "read_env", side_effect=lambda: dict(self.env)
        )
        self.write_env = patch.object(
            modern_app,
            "write_env",
            side_effect=lambda values: self.env.update(
                {str(key): str(value) for key, value in values.items()}
            ),
        )
        self.read_env.start()
        self.write_env.start()
        self.addCleanup(self.read_env.stop)
        self.addCleanup(self.write_env.stop)
        self.addCleanup(self.client.close)

    def test_health_is_public_and_protected_settings_require_a_session(self) -> None:
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.text, "ok")

        settings = self.client.get("/api/settings")
        self.assertEqual(settings.status_code, 503)
        self.assertIn("尚未初始化", settings.json()["detail"])

    def test_setup_login_and_settings_use_the_same_authenticated_session(self) -> None:
        setup = self.client.post(
            "/api/auth/setup",
            json={
                "username": "admin_user",
                "password": "secret6",
                "password_confirmation": "secret6",
            },
        )
        self.assertEqual(setup.status_code, 200)
        self.assertTrue(self.env["WEBUI_ADMIN_PASSWORD_HASH"].startswith("pbkdf2_sha256:"))

        with patch.object(
            modern_app.backend,
            "public_settings",
            return_value={"config": {}, "env": {}, "secrets": {}, "builtin_sources": []},
        ):
            settings = self.client.get("/api/settings")
        self.assertEqual(settings.status_code, 200)
        self.assertEqual(settings.json()["config"], {})

        self.assertEqual(self.client.post("/api/auth/logout").status_code, 204)
        login = self.client.post(
            "/api/auth/login", json={"username": "admin_user", "password": "secret6"}
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["username"], "admin_user")

    def test_skip_auth_allows_a_trusted_intranet_installation(self) -> None:
        response = self.client.post("/api/auth/setup", json={"action": "skip"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.env["WEBUI_AUTH_ENABLED"], "false")

        with patch.object(
            modern_app.backend,
            "public_settings",
            return_value={"config": {}, "env": {}, "secrets": {}, "builtin_sources": []},
        ):
            settings = self.client.get("/api/settings")
        self.assertEqual(settings.status_code, 200)

    def test_trend_template_routes_require_the_same_session(self) -> None:
        self.assertEqual(
            self.client.post(
                "/api/auth/setup",
                json={
                    "username": "template_admin",
                    "password": "secret6",
                    "password_confirmation": "secret6",
                },
            ).status_code,
            200,
        )
        with patch.object(
            modern_app.backend,
            "list_trend_prompt_templates",
            return_value=[{"name": "模板", "text": "内容"}],
        ):
            listed = self.client.get("/api/trend/templates")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["name"], "模板")

        with patch.object(
            modern_app.backend,
            "save_trend_prompt_template",
            return_value=[{"name": "模板", "text": "内容"}],
        ) as save:
            saved = self.client.put(
                "/api/trend/templates", json={"name": "模板", "text": "内容"}
            )
        self.assertEqual(saved.status_code, 200)
        save.assert_called_once_with("模板", "内容")

        with patch.object(
            modern_app.backend,
            "delete_trend_prompt_template",
            return_value=[],
        ) as delete:
            deleted = self.client.post("/api/trend/templates/delete", json={"name": "模板"})
        self.assertEqual(deleted.status_code, 200)
        delete.assert_called_once_with("模板")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
