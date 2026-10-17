"""DeepSeek catalog/configuration checks using synthetic keys and no provider traffic."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import ai_agent as agent, ai_settings as settings, security


REAL_CRYPT = settings._crypt
CANONICAL = "deepseek-canonical-synthetic"


class SettingsFixture:
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="spiderfly-ai-settings-")))
        self.stack.enter_context(patch.object(settings, "AI_DIR", self.root / "ai"))
        self.stack.enter_context(patch.object(settings, "SECRET_PATH", self.root / "secrets" / "legacy.dpapi"))
        self.stack.enter_context(patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}))
        self.sealed = {}

        def synthetic_crypt(content, *, decrypt):
            if decrypt:
                return self.sealed[content]
            encrypted = b"synthetic-dpapi-" + hashlib.sha256(content).digest()
            self.sealed[encrypted] = content
            return encrypted

        self.crypt = self.stack.enter_context(patch.object(settings, "_crypt", side_effect=synthetic_crypt))

    @property
    def path(self):
        return settings.AI_DIR / "settings.json"

    def read(self):
        return json.loads(self.path.read_text("utf-8"))

    def write(self, value):
        settings.AI_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def seed(self, key="synthetic-original-key", model=CANONICAL):
        with patch.object(settings, "_official_models", return_value=[model]):
            return settings.save_settings({"api_key": key, "model": model})

    def legacy(self, key="synthetic-legacy-key", model="deepseek-legacy-model"):
        settings.SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        settings.SECRET_PATH.write_bytes(settings._crypt(key.encode(), decrypt=False))
        self.write({**settings.DEFAULTS, "model": model})
        return key

    def session(self, data=None, status=200):
        context, client, response = MagicMock(), MagicMock(), MagicMock()
        context.__enter__.return_value = client
        response.status_code = status
        response.ok = 200 <= status < 400
        response.json.return_value = {"data": [{"id": CANONICAL}]} if data is None else data
        client.get.return_value = response
        return context, client, response


class CatalogTests(SettingsFixture, unittest.TestCase):
    def test_draft_key_fetch_is_official_and_does_not_save_any_file(self):
        context, client, _ = self.session({"data": [{"id": CANONICAL}, {"id": "deepseek-other-new-model"}, {"id": CANONICAL}]})
        with patch.object(settings.requests, "Session", return_value=context):
            result = settings.fetch_models({"api_key": "synthetic-draft-key"})
        self.assertEqual(result, {"connected": True, "models": [CANONICAL, "deepseek-other-new-model"], "key_source": "input"})
        self.assertEqual(list(self.root.rglob("*")), [])
        self.crypt.assert_not_called()
        args, kwargs = client.get.call_args
        self.assertEqual(args, ("https://api.deepseek.com/models",))
        self.assertFalse(client.trust_env)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], (10, 20))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer synthetic-draft-key")
        client.post.assert_not_called()

    def test_empty_official_list_has_clear_error(self):
        context, _, _ = self.session({"data": []})
        with patch.object(settings.requests, "Session", return_value=context), self.assertRaisesRegex(ValueError, "未返回可选模型"):
            settings.fetch_models({"api_key": "synthetic-draft-key"})
        self.assertFalse(self.path.exists())

    def test_invalid_catalog_shapes_and_model_names_are_rejected(self):
        values = [[], {}, {"data": {}}, {"data": [None]}, {"data": [{}]}, {"data": [{"id": 12}]},
                  {"data": [{"id": ""}]}, {"data": [{"id": "two words"}]},
                  {"data": [{"id": "x" * 201}]}, {"data": [{"id": "model"}] * 501}]
        for value in values:
            with self.subTest(value_type=type(value).__name__):
                context, _, _ = self.session(value)
                with patch.object(settings.requests, "Session", return_value=context), self.assertRaisesRegex(ValueError, "列表格式"):
                    settings.fetch_models({"api_key": "synthetic-draft-key"})

    def test_invalid_json_does_not_echo_provider_body(self):
        context, _, response = self.session()
        response.json.side_effect = ValueError("synthetic-private-provider-body")
        with patch.object(settings.requests, "Session", return_value=context), self.assertRaises(ValueError) as error:
            settings.fetch_models({"api_key": "synthetic-draft-key"})
        self.assertNotIn("synthetic-private-provider-body", str(error.exception))

    def test_redirect_is_not_followed_or_accepted(self):
        context, client, response = self.session(status=302)
        response.headers = {"Location": "https://synthetic-gateway.invalid"}
        with patch.object(settings.requests, "Session", return_value=context), self.assertRaisesRegex(ValueError, "拒绝重定向"):
            settings.fetch_models({"api_key": "synthetic-draft-key"})
        self.assertEqual(client.get.call_count, 1)
        self.assertFalse(client.get.call_args.kwargs["allow_redirects"])

    def test_http_401_reports_status_without_key_or_provider_body(self):
        context, _, response = self.session(status=401)
        response.text = "synthetic-private-provider-body"
        with patch.object(settings.requests, "Session", return_value=context), self.assertRaises(ValueError) as error:
            settings.fetch_models({"api_key": "synthetic-draft-key"})
        self.assertIn("401", str(error.exception))
        self.assertNotIn("synthetic-draft-key", str(error.exception))
        self.assertNotIn(response.text, str(error.exception))

    def test_network_failure_is_generic_and_does_not_save(self):
        context, client, _ = self.session()
        client.get.side_effect = requests.Timeout("synthetic-private-key-and-network-details")
        with patch.object(settings.requests, "Session", return_value=context), self.assertRaises(ValueError) as error:
            settings.fetch_models({"api_key": "synthetic-draft-key"})
        self.assertIn("连接失败", str(error.exception))
        self.assertNotIn("synthetic-private", str(error.exception))
        self.assertFalse(self.path.exists())

    def test_discovery_rejects_unknown_fields_or_invalid_key_without_network(self):
        values = [{"provider": "other"}, {"base_url": "https://example.invalid"}, {"model": CANONICAL},
                  {"api_key": None}, {"api_key": "x" * 501}, {"api_key": "bad\nkey"}, [], None]
        with patch.object(settings, "_official_models") as official:
            for value in values:
                with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                    settings.fetch_models(value)
        official.assert_not_called()

    def test_blank_key_uses_saved_key_without_writing(self):
        self.seed()
        before = self.path.read_bytes()
        with patch.object(settings, "_official_models", return_value=[CANONICAL]) as official:
            result = settings.fetch_models({"api_key": "  "})
        official.assert_called_once_with("synthetic-original-key")
        self.assertEqual(result["key_source"], "saved")
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_key_rejects_before_provider_request(self):
        with patch.object(settings, "_official_models") as official, self.assertRaisesRegex(ValueError, "尚未配置"):
            settings.fetch_models({})
        official.assert_not_called()


class ConfigurationTests(SettingsFixture, unittest.TestCase):
    def test_get_has_explicit_public_fields_and_no_network_or_decryption(self):
        self.seed()
        raw = self.read(); raw["api_key"] = "synthetic-unexpected-plaintext"; raw["unknown"] = {"private": "hidden"}; self.write(raw)
        expected = {*settings.DEFAULTS, "provider", "base_url", "models", "catalog_checked_at", "catalog_source", "key_source", "key_configured"}
        with patch.object(settings, "_official_models") as official, patch.object(settings, "_crypt") as crypt:
            public = settings.settings()
        self.assertEqual(set(public), expected)
        self.assertEqual(public["catalog_source"], "saved")
        self.assertEqual(public["key_source"], "saved")
        self.assertNotIn("synthetic-unexpected-plaintext", json.dumps(public))
        self.assertNotIn(settings._PRIVATE_KEY, public)
        official.assert_not_called(); crypt.assert_not_called()

    def test_new_install_does_not_offer_unverified_hardcoded_choices(self):
        value = settings.settings()
        self.assertEqual(value["model"], settings.DEFAULTS["model"])
        self.assertEqual(value["models"], [])
        self.assertEqual(value["catalog_source"], "none")
        self.assertIsNone(value["catalog_checked_at"])
        self.assertEqual(value["key_source"], "none")
        self.assertFalse(value["key_configured"])

    def test_legacy_current_model_is_preserved_without_claiming_official_catalog(self):
        self.write({**settings.DEFAULTS, "model": "deepseek-legacy-model"})
        value = settings.settings()
        self.assertEqual(value["model"], "deepseek-legacy-model")
        self.assertEqual(value["models"], ["deepseek-legacy-model"])
        self.assertEqual(value["catalog_source"], "current_only")
        self.assertIsNone(value["catalog_checked_at"])

    def test_dynamic_canonical_model_key_catalog_commit_in_single_file(self):
        with patch.object(settings, "_official_models", return_value=[CANONICAL]) as official, patch.object(settings, "atomic_write", wraps=settings.atomic_write) as write:
            result = settings.save_settings({"model": CANONICAL, "api_key": "synthetic-new-key"})
        official.assert_called_once_with("synthetic-new-key")
        self.assertEqual(write.call_count, 1)
        self.assertEqual(write.call_args.args[0], self.path)
        self.assertEqual(result["model"], CANONICAL)
        self.assertEqual(result["models"], [CANONICAL])
        self.assertEqual(result["catalog_source"], "saved")
        self.assertTrue(result["catalog_checked_at"])
        self.assertEqual(settings.api_key(), "synthetic-new-key")
        self.assertFalse(settings.SECRET_PATH.exists())
        self.assertNotIn(b"synthetic-new-key", self.path.read_bytes())
        self.assertIn(settings._PRIVATE_KEY, self.read())

    def test_model_save_rechecks_catalog_even_when_model_is_unchanged(self):
        self.seed()
        before = self.path.read_bytes()
        with patch.object(settings, "_official_models", return_value=["different-official-model"]) as official, self.assertRaisesRegex(ValueError, "不在当前密钥"):
            settings.save_settings({"model": CANONICAL})
        official.assert_called_once_with("synthetic-original-key")
        self.assertEqual(self.path.read_bytes(), before)

    def test_new_key_rechecks_current_model_and_cannot_half_save_key(self):
        self.seed()
        before = self.path.read_bytes()
        with patch.object(settings, "_official_models", return_value=["different-official-model"]), self.assertRaisesRegex(ValueError, "不在当前密钥"):
            settings.save_settings({"api_key": "synthetic-rejected-key"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(settings.api_key(), "synthetic-original-key")

    def test_failed_atomic_replace_preserves_old_key_model_catalog_and_legacy_file(self):
        self.legacy()
        self.seed()
        before, legacy_before = self.path.read_bytes(), settings.SECRET_PATH.read_bytes()
        with patch.object(settings, "_official_models", return_value=["new-official-model"]), patch.object(settings.os, "replace", side_effect=OSError("synthetic replacement failure")), self.assertRaises(OSError):
            settings.save_settings({"model": "new-official-model", "api_key": "synthetic-uncommitted-key"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(settings.SECRET_PATH.read_bytes(), legacy_before)
        self.assertEqual(settings.api_key(), "synthetic-original-key")
        self.assertEqual(list(settings.AI_DIR.glob(".ai-*")), [])

    def test_legacy_secret_can_be_used_to_validate_and_save_new_model_without_rewriting_key(self):
        key = self.legacy()
        secret_before = settings.SECRET_PATH.read_bytes()
        with patch.object(settings, "_official_models", return_value=[CANONICAL]) as official:
            settings.save_settings({"model": CANONICAL})
        official.assert_called_once_with(key)
        self.assertEqual(settings.SECRET_PATH.read_bytes(), secret_before)
        self.assertNotIn(settings._PRIVATE_KEY, self.read())
        self.assertEqual(settings.api_key(), key)

    def test_bad_new_private_key_never_falls_back_to_legacy_secret(self):
        self.legacy()
        raw = self.read(); raw[settings._PRIVATE_KEY] = "invalid base64"; self.write(raw)
        with patch.object(settings, "_crypt") as crypt, self.assertRaisesRegex(ValueError, "无法读取"):
            settings.api_key()
        crypt.assert_not_called()

    def test_undecryptable_new_private_key_never_falls_back(self):
        self.legacy()
        raw = self.read(); raw[settings._PRIVATE_KEY] = base64.b64encode(b"synthetic-corrupt-ciphertext").decode(); self.write(raw)
        with patch.object(settings, "_crypt", side_effect=ValueError("synthetic decrypt failure")) as crypt, self.assertRaisesRegex(ValueError, "无法读取"):
            settings.api_key()
        self.assertEqual(crypt.call_count, 1)

    def test_environment_key_wins_and_no_dpapi_is_used_or_created(self):
        self.seed()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": " synthetic-environment-key "}), patch.object(settings, "_official_models", return_value=[CANONICAL]) as official, patch.object(settings, "_crypt") as crypt:
            self.assertEqual(settings.api_key(), "synthetic-environment-key")
            result = settings.save_settings({"model": CANONICAL, "api_key": ""})
            self.assertEqual(settings.fetch_models({})["key_source"], "environment")
        self.assertEqual(result["key_source"], "environment")
        self.assertTrue(all(call.args == ("synthetic-environment-key",) for call in official.call_args_list))
        crypt.assert_not_called()

    def test_environment_key_rejects_nonempty_web_key_for_fetch_and_save(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-environment-key"}), patch.object(settings, "_official_models") as official:
            for operation, payload in ((settings.fetch_models, {"api_key": "synthetic-input-key"}), (settings.save_settings, {"model": CANONICAL, "api_key": "synthetic-input-key"})):
                with self.subTest(operation=operation.__name__), self.assertRaisesRegex(ValueError, "环境变量"):
                    operation(payload)
        official.assert_not_called(); self.assertFalse(self.path.exists())

    def test_whitespace_environment_is_not_a_configured_key(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": " \t "}):
            self.assertFalse(settings.settings()["key_configured"])
            self.seed()
            self.assertEqual(settings.api_key(), "synthetic-original-key")
            self.assertEqual(settings.settings()["key_source"], "saved")

    def test_environment_change_during_validation_aborts_without_file_change(self):
        self.seed()
        before = self.path.read_bytes()
        def changed(_key):
            os.environ["DEEPSEEK_API_KEY"] = "synthetic-changed-environment-key"
            return [CANONICAL]
        with patch.object(settings, "_official_models", side_effect=changed), self.assertRaisesRegex(ValueError, "校验期间发生变化"):
            settings.save_settings({"model": CANONICAL})
        self.assertEqual(self.path.read_bytes(), before)

    def test_budget_only_save_has_no_network_and_keeps_twenty_minute_limit(self):
        self.seed()
        before = self.read()
        with patch.object(settings, "_official_models") as official, patch.object(settings, "_crypt") as crypt:
            result = settings.save_settings({"max_calls": 96, "max_tokens": 1000000, "max_seconds": 7200})
        official.assert_not_called(); crypt.assert_not_called()
        self.assertEqual((result["max_calls"], result["max_tokens"], result["max_seconds"]), (96, 1000000, 1200))
        after = self.read()
        for name in ("model", "models", "catalog_checked_at", settings._PRIVATE_KEY):
            self.assertEqual(before[name], after[name])

    def test_unsupported_fields_and_bad_limits_fail_before_network(self):
        values = [{"provider": "other"}, {"base_url": "https://example.invalid"}, {settings._PRIVATE_KEY: "injected"},
                  {"models": [CANONICAL]}, {"model": ""}, {"model": {}}, {"max_calls": True}, {"max_calls": 129},
                  {"max_tokens": 2000001}, {"max_seconds": 29}, {"api_key": None}]
        with patch.object(settings, "_official_models") as official:
            for value in values:
                with self.subTest(fields=list(value)), self.assertRaises(ValueError):
                    settings.save_settings(value)
        official.assert_not_called()

    def test_serial_saves_cannot_validate_a_key_then_keep_another_requests_key(self):
        self.seed()
        entered, release = threading.Event(), threading.Event()
        seen = []
        def catalog(key):
            seen.append(key)
            if len(seen) == 1:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("test release timeout")
            return [CANONICAL]
        with patch.object(settings, "_official_models", side_effect=catalog), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(settings.save_settings, {"model": CANONICAL, "api_key": "synthetic-first-new-key"})
            self.assertTrue(entered.wait(3))
            second = pool.submit(settings.save_settings, {"model": CANONICAL, "api_key": ""})
            self.assertFalse(second.done())
            release.set()
            first.result(timeout=5); second.result(timeout=5)
        self.assertEqual(seen, ["synthetic-first-new-key", "synthetic-first-new-key"])
        self.assertEqual(settings.api_key(), "synthetic-first-new-key")

    def test_generation_http_wait_does_not_block_a_configuration_save(self):
        self.seed(model="deepseek-legacy-model")
        frozen = {key: settings.settings()[key] for key in settings.DEFAULTS}
        entered, release = threading.Event(), threading.Event()
        context, client, response = self.session()
        response.json.return_value = {"choices": [{"message": {"content": "synthetic generation"}}]}
        def generation(*_args, **_kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test release timeout")
            return response
        client.post.side_effect = generation
        with patch.object(settings.requests, "Session", return_value=context), patch.object(settings, "_official_models", return_value=[CANONICAL]), ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(settings.model_request, [], [], frozen, remaining=1000)
            self.assertTrue(entered.wait(3))
            try:
                saved = pool.submit(settings.save_settings, {"model": CANONICAL, "api_key": "synthetic-next-key"}).result(timeout=2)
                self.assertEqual(saved["model"], CANONICAL)
            finally:
                release.set()
            pending.result(timeout=3)
        self.assertEqual(client.post.call_args.kwargs["json"]["model"], "deepseek-legacy-model")
        self.assertFalse(client.post.call_args.kwargs["allow_redirects"])
        self.assertEqual(settings.api_key(), "synthetic-next-key")

    def test_pending_catalog_save_does_not_block_readers_of_last_committed_settings_or_key(self):
        self.seed(model="deepseek-legacy-model")
        entered, release = threading.Event(), threading.Event()
        def catalog(_key):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test release timeout")
            return [CANONICAL]
        with patch.object(settings, "_official_models", side_effect=catalog), ThreadPoolExecutor(max_workers=3) as pool:
            saving = pool.submit(settings.save_settings, {"model": CANONICAL, "api_key": "synthetic-pending-key"})
            self.assertTrue(entered.wait(3))
            try:
                old_settings = pool.submit(settings.settings).result(timeout=2)
                old_key = pool.submit(settings.api_key).result(timeout=2)
                self.assertEqual(old_settings["model"], "deepseek-legacy-model")
                self.assertEqual(old_key, "synthetic-original-key")
            finally:
                release.set()
            saving.result(timeout=3)
        self.assertEqual(settings.settings()["model"], CANONICAL)
        self.assertEqual(settings.api_key(), "synthetic-pending-key")

    def test_test_connection_uses_saved_effective_key_without_saving_catalog(self):
        self.seed()
        before = self.path.read_bytes()
        with patch.object(settings, "_official_models", return_value=["different-official-model"]):
            result = settings.check_connection()
        self.assertTrue(result["connected"])
        self.assertFalse(result["selected_model_available"])
        self.assertEqual(result["key_source"], "saved")
        self.assertEqual(self.path.read_bytes(), before)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_real_dpapi_synthetic_key_roundtrip_in_combined_settings_file(self):
        with patch.object(settings, "_crypt", side_effect=REAL_CRYPT), patch.object(settings, "_official_models", return_value=[CANONICAL]):
            settings.save_settings({"model": CANONICAL, "api_key": "synthetic-real-dpapi-key"})
            self.assertEqual(settings.api_key(), "synthetic-real-dpapi-key")
            self.assertNotIn(b"synthetic-real-dpapi-key", self.path.read_bytes())
            settings.save_settings({"max_calls": 3, "api_key": ""})
            self.assertEqual(settings.api_key(), "synthetic-real-dpapi-key")


class HttpConfigurationTests(SettingsFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.app = FastAPI()
        self.app.include_router(agent.router)
        self.user = {"id": 123, "role": "super_admin"}
        self.app.dependency_overrides[security.ready_user] = lambda: self.user
        self.client = self.stack.enter_context(TestClient(self.app))
        self.audit = self.stack.enter_context(patch.object(agent, "write_audit"))

    def test_catalog_and_save_require_superadmin_before_network_or_mutation(self):
        with patch.object(settings, "_official_models") as official:
            for role in ("operator", "admin"):
                self.user = {"id": 123, "role": role}
                self.assertEqual(self.client.post("/api/ai/settings/models", json={"api_key": "synthetic-key"}).status_code, 403)
                self.assertEqual(self.client.put("/api/ai/settings", json={"api_key": "synthetic-key", "model": CANONICAL}).status_code, 403)
                self.assertEqual(self.client.post("/api/ai/settings/test").status_code, 403)
        official.assert_not_called(); self.assertFalse(self.path.exists()); self.audit.assert_not_called()

    def test_catalog_fetch_then_save_rechecks_and_never_echoes_key(self):
        with patch.object(settings, "_official_models", return_value=[CANONICAL]) as official:
            fetched = self.client.post("/api/ai/settings/models", json={"api_key": "synthetic-http-key"})
            self.assertEqual(fetched.status_code, 200)
            self.assertFalse(self.path.exists())
            saved = self.client.put("/api/ai/settings", json={"model": CANONICAL, "api_key": "synthetic-http-key"})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(official.call_count, 2)
        for response in (fetched, saved, self.client.get("/api/ai/settings")):
            self.assertNotIn("synthetic-http-key", response.text)
            self.assertNotIn(settings._PRIVATE_KEY, response.text)
        self.assertEqual(self.audit.call_count, 1)

    def test_bad_json_extra_fields_and_oversized_request_never_call_provider(self):
        with patch.object(settings, "_official_models") as official:
            for path, method in (("/api/ai/settings/models", "POST"), ("/api/ai/settings", "PUT")):
                for raw in ('{"api_key":', json.dumps({"provider": "other", "api_key": "synthetic-hidden-key"}), json.dumps({"api_key": "x" * 5000})):
                    response = self.client.request(method, path, content=raw, headers={"Content-Type": "application/json"})
                    self.assertEqual(response.status_code, 400)
                    self.assertNotIn("synthetic-hidden-key", response.text)
        official.assert_not_called(); self.audit.assert_not_called()

    def test_put_offloads_network_operation_from_async_event_loop(self):
        event_thread = []
        async def identity():
            event_thread.append(threading.get_ident())
            return self.user
        self.app.dependency_overrides[security.ready_user] = identity
        worker_threads = []
        def catalog(_key):
            worker_threads.append(threading.get_ident()); return [CANONICAL]
        with patch.object(settings, "_official_models", side_effect=catalog):
            response = self.client.put("/api/ai/settings", json={"model": CANONICAL, "api_key": "synthetic-offloaded-key"})
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(worker_threads, event_thread)

    def test_connection_network_errors_are_generic_at_http_boundary(self):
        self.seed()
        with patch.object(settings, "check_connection", side_effect=requests.Timeout("synthetic-private-provider-details")):
            response = self.client.post("/api/ai/settings/test")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("synthetic-private", response.text)

    def test_get_allows_admin_but_rejects_member_without_leaking_private_fields(self):
        self.seed()
        self.user = {"id": 123, "role": "admin"}
        response = self.client.get("/api/ai/settings")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(settings._PRIVATE_KEY, response.text)
        self.user = {"id": 123, "role": "operator"}
        self.assertEqual(self.client.get("/api/ai/settings").status_code, 403)


class FrozenTurnTests(unittest.TestCase):
    def test_new_default_does_not_migrate_an_existing_pending_turn_snapshot(self):
        import test_ai_agent as support
        helper = support.AgentTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        with patch.object(settings, "_official_models", return_value=["deepseek-legacy-model", CANONICAL]):
            settings.save_settings({"model": "deepseek-legacy-model"})
            queued = agent.send_message(helper.thread["id"], agent.Message(content="只回复合成文本"), helper.user)
            settings.save_settings({"model": CANONICAL})
        support.database.execute("UPDATE ai_turns SET status='running' WHERE id=?", (queued["id"],))
        turn = support.database.fetch_one("SELECT * FROM ai_turns WHERE id=?", (queued["id"],))
        reply = {"usage": {"prompt_tokens": 4, "completion_tokens": 5}, "choices": [{"message": {"role": "assistant", "content": "合成回复"}, "finish_reason": "stop"}]}
        with patch.object(settings, "model_request", return_value=reply) as model:
            asyncio.run(agent.process_turn(turn))
        self.assertEqual(model.call_args.args[2]["model"], "deepseek-legacy-model")
        self.assertEqual(settings.settings()["model"], CANONICAL)
        self.assertNotIn("api_key", turn["config"])


if __name__ == "__main__":
    unittest.main()
