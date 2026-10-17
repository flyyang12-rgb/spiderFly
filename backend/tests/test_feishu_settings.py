"""Synthetic notification settings: no delivery, credentials or production paths."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import feishu_settings as settings, security
from app.api import settings as api
from app.feishu import FeishuError, FeishuNotifier, FeishuSettings


HOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/synthetic-group"
REAL_CRYPT = settings._crypt


class SettingsFixture:
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="spiderfly-feishu-settings-")))
        self.path = self.root / "secrets" / "feishu.dpapi"
        self.stack.enter_context(patch.object(settings, "SETTINGS_PATH", self.path))
        self.stack.enter_context(patch.dict(os.environ, {name: "" for name in settings._ENV_FIELDS.values()}))
        self.sealed = {}
        def crypt(content, *, decrypt):
            if decrypt:
                return self.sealed[content]
            encrypted = b"synthetic-dpapi-" + hashlib.sha256(content).digest()
            self.sealed[encrypted] = content
            return encrypted
        self.stack.enter_context(patch.object(settings, "_crypt", side_effect=crypt))

    def save(self, **values):
        return settings.save_settings({"revision": settings.public_settings()["revision"], "mode": "webhook", "webhook_url": HOOK, **values})


class LocalSettingsTests(SettingsFixture, unittest.TestCase):
    def test_empty_configuration_creates_no_file_and_is_not_connected(self):
        value = settings.public_settings(include_details=True)
        self.assertFalse(value["configured"]); self.assertEqual(value["source"], "none")
        self.assertEqual(value["mode"], "webhook"); self.assertEqual(value["revision"], "none")
        self.assertFalse(FeishuSettings.from_env().configured); self.assertFalse(self.path.exists())

    def test_save_is_sealed_and_does_not_expose_secrets_or_touch_environment(self):
        before = dict(os.environ)
        with patch("app.feishu.requests.Session") as network:
            result = self.save(webhook_secret="synthetic-signing-secret")
        self.assertTrue(result["configured"]); self.assertTrue(result["webhook_secret_configured"])
        self.assertEqual(os.environ, before); network.assert_not_called()
        for secret in (HOOK, "synthetic-signing-secret"):
            self.assertNotIn(secret, json.dumps(result)); self.assertNotIn(secret.encode(), self.path.read_bytes())
        self.assertEqual(FeishuSettings.from_env().webhook_url, HOOK)

    def test_blank_sensitive_fields_preserve_both_secrets_and_optional_signature_can_be_cleared(self):
        self.save(webhook_secret="synthetic-signing-secret")
        self.save(webhook_url="", webhook_secret="")
        self.assertEqual(FeishuSettings.from_env().webhook_secret, "synthetic-signing-secret")
        self.save(webhook_url="", clear_webhook_secret=True)
        self.assertEqual(FeishuSettings.from_env().webhook_secret, "")

    def test_personal_mobile_and_open_id_reuse_existing_notification_path(self):
        value = self.save(mode="application", app_id="cli_synthetic", app_secret="synthetic-app-secret", receiver_id="13800000000", receiver_id_type="mobile")
        self.assertEqual(value["mode"], "application")
        effective = FeishuSettings.from_env()
        self.assertEqual(effective.receiver_id_type, "mobile"); self.assertEqual(effective.webhook_url, "")
        self.save(mode="application", app_secret="", receiver_id="ou_synthetic", receiver_id_type="open_id")
        self.assertEqual(FeishuSettings.from_env().app_secret, "synthetic-app-secret")

    def test_mode_switch_uses_one_audience_and_preserves_inactive_profile(self):
        self.save(webhook_secret="synthetic-signature")
        self.save(mode="application", app_id="cli_synthetic", app_secret="synthetic-app-secret", receiver_id="13800000000", receiver_id_type="mobile")
        self.assertEqual(FeishuSettings.from_env().webhook_url, "")
        self.save(webhook_url="")
        current = FeishuSettings.from_env()
        self.assertEqual(current.webhook_url, HOOK); self.assertEqual(current.app_secret, "")
        self.assertEqual(settings.public_settings(include_details=True)["receiver_id"], "13800000000")

    def test_new_notifier_observes_save_existing_notifier_keeps_notification_snapshot(self):
        self.save(); first = FeishuNotifier()
        second_url = HOOK + "-new"
        self.save(webhook_url=second_url)
        second = FeishuNotifier()
        self.assertEqual(first.settings.webhook_url, HOOK); self.assertEqual(second.settings.webhook_url, second_url)
        self.assertEqual(second._token, ""); self.assertIsNot(first._token_lock, second._token_lock)

    def test_environment_has_whole_configuration_priority_and_is_read_only(self):
        self.save()
        with patch.dict(os.environ, {"FEISHU_APP_ID": "env-app", "FEISHU_APP_SECRET": "env-secret", "FEISHU_RECEIVER_ID": "env-user"}):
            effective = FeishuSettings.from_env()
            self.assertEqual(effective.webhook_url, ""); self.assertEqual(effective.app_id, "env-app")
            self.assertEqual(settings.public_settings()["source"], "environment")
            with self.assertRaises(settings.ConfigurationConflict): self.save()
            with self.assertRaises(settings.ConfigurationConflict): settings.clear_settings({"revision": "none"})
        self.assertEqual(FeishuSettings.from_env().webhook_url, HOOK)

    def test_partial_environment_never_combines_with_local_audience(self):
        self.save()
        with patch.dict(os.environ, {"FEISHU_APP_ID": "partial-env-app"}):
            self.assertFalse(FeishuSettings.from_env().configured)
            self.assertFalse(settings.public_settings()["configured"])
            self.assertTrue(settings.public_settings()["read_only"])

    def test_legacy_environment_webhook_and_application_fields_remain_compatible(self):
        with patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": "https://example.com/legacy", "FEISHU_WEBHOOK_SECRET": "env-signature", "FEISHU_APP_ID": "legacy-app", "FEISHU_APP_SECRET": "legacy-secret", "FEISHU_RECEIVER_ID_TYPE": "invalid"}):
            value = FeishuSettings.from_env()
            self.assertEqual(value.webhook_url, "https://example.com/legacy"); self.assertEqual(value.app_secret, "legacy-secret")
            self.assertEqual(value.receiver_id_type, "open_id")

    def test_validation_cannot_mutate_saved_configuration(self):
        self.save(); before = self.path.read_bytes()
        invalid = [{"webhook_url": "http://open.feishu.cn/open-apis/bot/v2/hook/a"},
                   {"webhook_url": "https://example.com/private"}, {"webhook_url": HOOK + "?secret=1"},
                   {"webhook_url": HOOK + "#fragment"}, {"webhook_url": HOOK.replace(".cn/", ".cn:444/")},
                   {"webhook_url": HOOK.replace("https://", "https://user@")}, {"webhook_url": "https://127.0.0.1/a"},
                   {"webhook_secret": []}, {"webhook_secret": "a\nsecret"}, {"clear_webhook_secret": 1},
                   {"webhook_secret": "new", "clear_webhook_secret": True}, {"unexpected": HOOK},
                   {"mode": "other"}, {"mode": "application", "receiver_id_type": "other"},
                   {"mode": "application", "app_id": "cli_changed"},
                   {"mode": "application", "app_id": "cli_a", "app_secret": "a", "receiver_id": "not-phone"}]
        for values in invalid:
            with self.subTest(values=list(values)):
                with self.assertRaises(ValueError): self.save(**values)
                self.assertEqual(self.path.read_bytes(), before)

    def test_wrong_revision_and_two_concurrent_edits_do_not_overwrite(self):
        initial = self.save(); revision = initial["revision"]
        gate = threading.Barrier(2)
        def mutate(suffix):
            gate.wait()
            try: settings.save_settings({"revision": revision, "mode": "webhook", "webhook_url": HOOK + suffix}); return "saved"
            except settings.ConfigurationConflict: return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(mutate, ("-a", "-b")))
        self.assertCountEqual(results, ["saved", "conflict"])
        with self.assertRaises(settings.ConfigurationConflict): settings.clear_settings({"revision": revision})

    def test_disk_or_encryption_failure_preserves_last_saved_file(self):
        self.save(); before = self.path.read_bytes()
        with patch.object(settings, "atomic_write", side_effect=OSError("synthetic-private-path")):
            with self.assertRaisesRegex(ValueError, "保存失败"): self.save(webhook_url=HOOK + "-new")
        self.assertEqual(self.path.read_bytes(), before)
        with patch.object(settings, "_crypt", side_effect=ValueError("private-dpapi")):
            with self.assertRaisesRegex(ValueError, "无法读取"): self.save()
        self.assertEqual(self.path.read_bytes(), before)

    def test_broken_ciphertext_has_safe_status_can_clear_and_notifier_does_not_break_task(self):
        self.save(); self.sealed.clear()
        value = settings.public_settings()
        self.assertTrue(value["configuration_error"]); self.assertFalse(value["configured"])
        notifier = FeishuNotifier()
        self.assertTrue(notifier.configured)
        with patch.object(notifier._session, "post") as delivery:
            with self.assertRaisesRegex(FeishuError, "配置无法读取"): notifier.send_final_result(task_name="合成任务", status="failed", duration_ms=0)
        delivery.assert_not_called()
        settings.clear_settings({"revision": value["revision"]})
        self.assertFalse(self.path.exists()); self.assertFalse(settings.public_settings()["configured"])

    def test_clear_is_explicit_and_encompasses_both_profiles(self):
        self.save(); self.save(mode="application", app_id="cli_synthetic", app_secret="app-secret", receiver_id="13800000000", receiver_id_type="mobile")
        result = settings.clear_settings({"revision": settings.public_settings()["revision"]})
        self.assertEqual(result["source"], "none"); self.assertFalse(self.path.exists())
        self.assertFalse(FeishuSettings.from_env().configured)

    def test_atomic_replace_failure_keeps_original_and_removes_temporary_file(self):
        self.save(); before = self.path.read_bytes()
        with patch("app.ai_settings.os.replace", side_effect=OSError("synthetic-replace-failure")):
            with self.assertRaisesRegex(ValueError, "保存失败"): self.save(webhook_url=HOOK + "-new")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([item.name for item in self.path.parent.iterdir()], [self.path.name])

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_real_current_account_dpapi_round_trip_contains_no_plaintext(self):
        with patch.object(settings, "_crypt", side_effect=REAL_CRYPT):
            result = self.save(webhook_secret="synthetic-real-dpapi-signature")
            self.assertTrue(result["configured"])
            self.assertEqual(FeishuSettings.from_env().webhook_secret, "synthetic-real-dpapi-signature")
        self.assertNotIn(HOOK.encode(), self.path.read_bytes())
        self.assertNotIn(b"synthetic-real-dpapi-signature", self.path.read_bytes())


class HttpSettingsTests(SettingsFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.app = FastAPI(); self.app.include_router(api.router)
        self.user = {"id": 123, "role": "super_admin"}
        self.app.dependency_overrides[security.ready_user] = lambda: self.user
        self.client = self.stack.enter_context(TestClient(self.app))
        self.audit = self.stack.enter_context(patch.object(api, "write_audit"))

    def test_superadmin_can_save_without_delivery_and_responses_and_audit_hide_secrets(self):
        with patch("app.feishu.requests.Session") as network:
            response = self.client.put("/api/settings/feishu", json={"revision": "none", "mode": "webhook", "webhook_url": HOOK, "webhook_secret": "synthetic-signature"})
        self.assertEqual(response.status_code, 200); network.assert_not_called()
        for result in (response, self.client.get("/api/settings/feishu"), self.client.get("/api/settings")):
            self.assertNotIn(HOOK, result.text); self.assertNotIn("synthetic-signature", result.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn(HOOK, str(self.audit.call_args)); self.audit.assert_called_once()

    def test_admin_only_reads_status_member_and_anonymous_denied_before_decryption(self):
        self.save(mode="application", app_id="cli_private", app_secret="private-secret", receiver_id="13800000000", receiver_id_type="mobile")
        self.user["role"] = "admin"
        result = self.client.get("/api/settings/feishu")
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("receiver_id", result.json()); self.assertNotIn("cli_private", result.text)
        self.assertEqual(self.client.put("/api/settings/feishu", json={}).status_code, 403)
        self.assertEqual(self.client.delete("/api/settings/feishu").status_code, 403)
        self.user["role"] = "operator"
        with patch.object(settings, "public_settings") as read:
            self.assertEqual(self.client.get("/api/settings/feishu").status_code, 403)
            self.app.dependency_overrides.clear()
            self.assertEqual(self.client.get("/api/settings/feishu").status_code, 401)
        read.assert_not_called()

    def test_malformed_unknown_and_oversized_input_does_not_write_or_echo(self):
        for raw in ('{"webhook_url":', json.dumps({"revision": "none", "mode": "webhook", "secret": HOOK}), "[]", json.dumps({"webhook_url": "x" * 5000})):
            result = self.client.put("/api/settings/feishu", content=raw, headers={"Content-Type": "application/json"})
            self.assertEqual(result.status_code, 400); self.assertNotIn(HOOK, result.text)
            self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertFalse(self.path.exists()); self.audit.assert_not_called()

    def test_stale_save_returns_conflict_and_delete_requires_matching_revision(self):
        initial = self.save()
        response = self.client.put("/api/settings/feishu", json={"revision": "none", "mode": "webhook", "webhook_url": HOOK})
        self.assertEqual(response.status_code, 409); self.audit.assert_not_called()
        response = self.client.request("DELETE", "/api/settings/feishu", json={"revision": initial["revision"]})
        self.assertEqual(response.status_code, 200); self.assertFalse(self.path.exists())

    def test_secret_writing_is_offloaded_and_never_blocks_async_event_loop(self):
        event_thread, worker_thread = [], []
        async def identity(): event_thread.append(threading.get_ident()); return self.user
        self.app.dependency_overrides[security.ready_user] = identity
        original = settings.atomic_write
        def write(path, content): worker_thread.append(threading.get_ident()); return original(path, content)
        with patch.object(settings, "atomic_write", side_effect=write):
            result = self.client.put("/api/settings/feishu", json={"revision": "none", "mode": "webhook", "webhook_url": HOOK})
        self.assertEqual(result.status_code, 200); self.assertNotEqual(event_thread, worker_thread)


if __name__ == "__main__":
    unittest.main()
