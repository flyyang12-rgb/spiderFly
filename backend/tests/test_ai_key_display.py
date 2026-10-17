"""Saved-key presentation uses a dedicated super-admin response, synthetic keys only."""
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import ai_agent, ai_settings, security
from test_ai_settings import SettingsFixture


class KeyDisplayTests(SettingsFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.app = FastAPI(); self.app.include_router(ai_agent.router)
        self.user = {"id": 123, "role": "super_admin"}
        self.app.dependency_overrides[security.ready_user] = lambda: self.user
        self.client = self.stack.enter_context(TestClient(self.app))

    def test_superadmin_can_read_saved_key_without_exposing_ciphertext_or_writing(self):
        self.seed("synthetic-display-key")
        before = self.path.read_bytes()
        with patch.object(ai_settings.requests, "Session") as provider, patch.object(ai_agent, "write_audit") as audit:
            response = self.client.get("/api/ai/settings/key")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"api_key": "synthetic-display-key", "key_source": "saved"})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["pragma"], "no-cache")
        self.assertNotIn(ai_settings._PRIVATE_KEY, response.text)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn(b"synthetic-display-key", before)
        provider.assert_not_called(); audit.assert_not_called()

    def test_environment_key_is_displayed_as_the_effective_key(self):
        self.seed("synthetic-file-key")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-environment-key"}):
            response = self.client.get("/api/ai/settings/key")
        self.assertEqual(response.json(), {"api_key": "synthetic-environment-key", "key_source": "environment"})

    def test_admin_operator_and_anonymous_are_denied_before_decryption(self):
        with patch.object(ai_settings, "display_key") as read:
            for role in ("admin", "operator"):
                self.user["role"] = role
                self.assertEqual(self.client.get("/api/ai/settings/key").status_code, 403)
            self.app.dependency_overrides.clear()
            self.assertEqual(self.client.get("/api/ai/settings/key").status_code, 401)
        read.assert_not_called()

    def test_public_configuration_and_frozen_config_stay_key_free(self):
        self.seed("synthetic-public-exclusion")
        response = self.client.get("/api/ai/settings")
        self.assertNotIn("api_key", response.json())
        self.assertNotIn("synthetic-public-exclusion", response.text)
        self.assertNotIn("api_key", ai_settings.settings())
        self.user["role"] = "admin"
        self.assertNotIn("synthetic-public-exclusion", self.client.get("/api/ai/settings").text)

    def test_empty_configuration_returns_empty_key_without_network(self):
        with patch.object(ai_settings.requests, "Session") as provider:
            response = self.client.get("/api/ai/settings/key")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"api_key": "", "key_source": "none"})
        provider.assert_not_called(); self.assertFalse(self.path.exists())

    def test_broken_saved_key_error_does_not_block_replacement_or_leak_details(self):
        self.seed()
        self.sealed.clear()
        response = self.client.get("/api/ai/settings/key")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("synthetic-original-key", response.text)
        self.assertEqual(self.client.get("/api/ai/settings").status_code, 200)
