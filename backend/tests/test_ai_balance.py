"""Account balance is an authenticated read using only synthetic provider replies."""
import asyncio
import json
import threading
import unittest
from unittest.mock import patch

import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import ai_agent, ai_settings, security
from test_ai_settings import SettingsFixture


BALANCE = {"is_available": True, "balance_infos": [
    {"currency": "CNY", "total_balance": "12.34567890", "granted_balance": "2.00", "topped_up_balance": "10.34567890"}
]}


class BalanceTests(SettingsFixture, unittest.TestCase):
    def query(self, payload=BALANCE, status=200):
        context, client, response = self.session(payload, status)
        with patch.object(ai_settings.requests, "Session", return_value=context):
            result = ai_settings.account_balance()
        return result, client

    def test_official_saved_key_read_preserves_precision_and_does_not_write_or_generate(self):
        self.seed()
        before = self.path.read_bytes()
        result, client = self.query({**BALANCE, "private": "must not escape"})
        self.assertEqual(result["balance_infos"], BALANCE["balance_infos"])
        self.assertTrue(result["is_available"])
        self.assertTrue(result["checked_at"].endswith("+00:00"))
        self.assertEqual(set(result), {"balance_infos", "is_available", "checked_at"})
        self.assertEqual(self.path.read_bytes(), before)
        args, kwargs = client.get.call_args
        self.assertEqual(args, ("https://api.deepseek.com/user/balance",))
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer synthetic-original-key"})
        self.assertEqual(kwargs["timeout"], (10, 20))
        self.assertFalse(client.trust_env); self.assertFalse(kwargs["allow_redirects"])
        client.post.assert_not_called()

    def test_environment_key_has_priority(self):
        self.seed()
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "synthetic-environment-key"}):
            _, client = self.query()
        self.assertEqual(client.get.call_args.kwargs["headers"]["Authorization"], "Bearer synthetic-environment-key")

    def test_missing_key_never_contacts_provider(self):
        with patch.object(ai_settings.requests, "Session") as provider, self.assertRaises(ValueError):
            ai_settings.account_balance()
        provider.assert_not_called()

    def test_zero_negative_and_multiple_currency_amounts_are_not_rounded_or_converted(self):
        self.seed()
        rows = [{"currency": currency, "total_balance": amount, "granted_balance": "0", "topped_up_balance": amount}
                for currency, amount in (("CNY", "0.00"), ("USD", "-0.001"))]
        result, _ = self.query({"is_available": False, "balance_infos": rows})
        self.assertFalse(result["is_available"]); self.assertEqual(result["balance_infos"], rows)

    def test_bad_shapes_currencies_and_amounts_are_rejected_without_echo(self):
        self.seed()
        row = BALANCE["balance_infos"][0]
        bad = [None, [], {}, {**BALANCE, "is_available": 1}, {**BALANCE, "balance_infos": []},
               {**BALANCE, "balance_infos": [row, row]}, {**BALANCE, "balance_infos": [None]}]
        for field, value in (("currency", "secret"), ("total_balance", 12.3), ("granted_balance", "NaN"),
                             ("topped_up_balance", "1e100"), ("total_balance", "9" * 100)):
            bad.append({**BALANCE, "balance_infos": [{**row, field: value}]})
        for payload in bad:
            # Fixture's None means default catalogue; also an invalid balance shape.
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "余额格式无效"):
                self.query(payload)

    def test_provider_status_redirect_json_and_network_failures_are_sanitized(self):
        self.seed()
        for status in (301, 302, 401, 429, 500):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, f"HTTP {status}"):
                self.query({"error": "synthetic-private-value"}, status)
        context, client, response = self.session(BALANCE)
        response.json.side_effect = ValueError("synthetic-private-json")
        with patch.object(ai_settings.requests, "Session", return_value=context), self.assertRaisesRegex(ValueError, "余额格式无效"):
            ai_settings.account_balance()
        response.json.side_effect = None
        client.get.side_effect = requests.Timeout("synthetic-private-network")
        with patch.object(ai_settings.requests, "Session", return_value=context), self.assertRaisesRegex(ValueError, "余额连接失败"):
            ai_settings.account_balance()

    def test_changed_key_during_query_discards_previous_account_result(self):
        self.seed()
        context, client, response = self.session(BALANCE)
        client.get.side_effect = lambda *args, **kwargs: (self.seed("synthetic-replacement-key"), response)[1]
        with patch.object(ai_settings.requests, "Session", return_value=context), self.assertRaisesRegex(ValueError, "密钥已改变"):
            ai_settings.account_balance()

    def test_settings_read_remains_offline_and_contains_no_balance(self):
        self.seed()
        with patch.object(ai_settings.requests, "Session") as provider:
            value = ai_settings.settings()
        provider.assert_not_called(); self.assertNotIn("balance_infos", value)


class BalanceHttpTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI(); self.app.include_router(ai_agent.router)
        self.user = {"id": 5, "role": "super_admin"}
        self.app.dependency_overrides[security.ready_user] = lambda: self.user
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_admin_read_operator_denied_and_no_secret_or_mutation(self):
        with patch.object(ai_settings, "account_balance", return_value=BALANCE) as provider:
            for role in ("super_admin", "admin"):
                self.user["role"] = role
                response = self.client.get("/api/ai/settings/balance")
                self.assertEqual(response.status_code, 200)
            self.user["role"] = "operator"
            self.assertEqual(self.client.get("/api/ai/settings/balance").status_code, 403)
        self.assertEqual(provider.call_count, 2)

    def test_unauthenticated_request_rejected_before_provider(self):
        self.app.dependency_overrides.clear()
        with patch.object(ai_settings, "account_balance") as provider:
            self.assertEqual(self.client.get("/api/ai/settings/balance").status_code, 401)
        provider.assert_not_called()

    def test_provider_401_maps_to_safe_400_instead_of_expiring_login(self):
        with patch.object(ai_settings, "account_balance", side_effect=ValueError("DeepSeek 余额查询失败（HTTP 401）")):
            response = self.client.get("/api/ai/settings/balance")
        self.assertEqual(response.status_code, 400)
        with patch.object(ai_settings, "account_balance", side_effect=RuntimeError("synthetic-secret")):
            response = self.client.get("/api/ai/settings/balance")
        self.assertEqual(response.status_code, 400); self.assertNotIn("synthetic-secret", response.text)

    def test_network_work_is_offloaded_and_permission_copy_is_generic(self):
        event_threads, workers = [], []
        async def identity():
            event_threads.append(threading.get_ident()); return self.user
        self.app.dependency_overrides[security.ready_user] = identity
        def query():
            workers.append(threading.get_ident()); return BALANCE
        with patch.object(ai_settings, "account_balance", side_effect=query):
            self.assertEqual(self.client.get("/api/ai/settings/balance").status_code, 200)
        self.assertNotEqual(event_threads, workers)
        self.user["role"] = "admin"
        response = self.client.post("/api/ai/settings/models", json={})
        self.assertEqual(response.json()["detail"], "需要超级管理员权限")
