from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

import requests

from app.feishu import FeishuError, FeishuNotifier, FeishuSettings


class FailureNotificationTests(unittest.TestCase):
    def test_manual_intervention_fields_are_separate_and_clickable(self) -> None:
        notifier = FeishuNotifier(
            FeishuSettings(
                app_id="test-app",
                app_secret="test-secret",
                receiver_id="test-user",
            )
        )
        notifier._request = MagicMock(return_value={})  # type: ignore[method-assign]

        notifier._send_failure_post(
            "ou_test",
            "登录态检查",
            1500,
            "登录状态已失效",
            None,
            result_code="LOGIN_EXPIRED",
            manual_action_url="https://example.com/login",
            manual_code="ACCOUNT_RELOGIN",
        )

        request = notifier._request.call_args
        payload = json.loads(request.kwargs["json_body"]["content"])
        rows = payload["zh_cn"]["content"]
        texts = [item.get("text", "") for row in rows for item in row]
        links = [item for row in rows for item in row if item.get("tag") == "a"]
        self.assertIn("结果编码：LOGIN_EXPIRED", texts)
        self.assertIn("人工处理编码：ACCOUNT_RELOGIN", texts)
        self.assertEqual(links[0]["href"], "https://example.com/login")


class WebhookNotificationTests(unittest.TestCase):
    def notifier(self, payload=None, status=200):
        notifier = FeishuNotifier(FeishuSettings(
            app_id="old-app", app_secret="old-secret", receiver_id="old-user",
            webhook_url="https://example.com/private-hook",
        ))
        response = MagicMock(status_code=status)
        response.json.return_value = {"code": 0} if payload is None else payload
        notifier._session = MagicMock()
        notifier._session.post.return_value = response
        notifier._request = MagicMock(side_effect=AssertionError("Personal delivery forbidden"))
        notifier._resolve_open_id = MagicMock(side_effect=AssertionError("Recipient lookup forbidden"))
        return notifier

    def test_webhook_alone_is_configured_from_env(self):
        with patch.dict("os.environ", {"FEISHU_WEBHOOK_URL": " https://example.com/hook "}, clear=True):
            settings = FeishuSettings.from_env()
        self.assertTrue(settings.configured)
        self.assertEqual(settings.webhook_url, "https://example.com/hook")
        self.assertFalse(settings.app_id)

    def test_success_uses_group_without_personal_lookup(self):
        notifier = self.notifier()
        notifier.send_final_result(task_name="合成测试", status="success", duration_ms=1500)
        payload = notifier._session.post.call_args.kwargs["json"]
        self.assertEqual(payload["msg_type"], "text")
        self.assertIn("SpiderFly", payload["content"]["text"])
        self.assertIn("运行成功", payload["content"]["text"])
        self.assertNotIn("receive_id", payload)
        notifier._session.post.assert_called_once()

    def test_failure_preserves_error_and_manual_action(self):
        notifier = self.notifier()
        notifier.send_final_result(task_name="合成测试", status="timeout", duration_ms=60000,
            error_summary="执行超时", result_code="TIMEOUT", manual_code="RETRY",
            manual_action_url="https://example.com/action")
        payload = notifier._session.post.call_args.kwargs["json"]
        self.assertEqual(payload["msg_type"], "post")
        rows = payload["content"]["post"]["zh_cn"]["content"]
        self.assertIn("执行超时", json.dumps(rows, ensure_ascii=False))
        self.assertIn("结果编码：TIMEOUT", json.dumps(rows, ensure_ascii=False))
        self.assertEqual(rows[-1][-1]["href"], "https://example.com/action")

    def test_legacy_webhook_success_response(self):
        self.notifier({"StatusCode": 0}).send_final_result(task_name="测试", status="success", duration_ms=0)

    def test_signed_webhook_matches_fixed_signature(self):
        notifier = self.notifier()
        notifier.settings = replace(notifier.settings, webhook_secret="webhook-test-secret")
        with patch("app.feishu.time.time", return_value=1599360473):
            notifier.send_final_result(task_name="测试", status="success", duration_ms=0)
        body = notifier._session.post.call_args.kwargs["json"]
        self.assertEqual(body["timestamp"], "1599360473")
        self.assertEqual(body["sign"], "ONhyyh4oVAol2BLGezgllMWxys7Cy4jYuJG/5f+PsSI=")
        self.assertNotIn("webhook-test-secret", json.dumps(body))

    def test_signing_secret_loaded_from_env(self):
        with patch.dict("os.environ", {"FEISHU_WEBHOOK_SECRET": " test-signing-secret "}, clear=True):
            settings = FeishuSettings.from_env()
        self.assertEqual(settings.webhook_secret, "test-signing-secret")
        self.assertFalse(settings.configured)

    def test_rejected_responses_do_not_retry_or_fall_back(self):
        for payload, status in [({"code": 19024}, 200), ({"StatusCode": 1}, 200),
                ({"code": 19021, "StatusCode": 0}, 200), ({}, 200), ([], 200),
                ({"code": 0}, 500), ({"code": 0}, 302)]:
            with self.subTest(payload=payload, status=status):
                notifier = self.notifier(payload, status)
                with self.assertRaises(FeishuError):
                    notifier.send_final_result(task_name="测试", status="failed", duration_ms=0)
                notifier._session.post.assert_called_once()
                notifier._request.assert_not_called()

    def test_network_failure_hides_webhook_secret(self):
        notifier = self.notifier()
        notifier._session.post.side_effect = requests.Timeout("https://example.com/private-hook")
        with self.assertRaises(FeishuError) as caught:
            notifier.send_final_result(task_name="测试", status="failed", duration_ms=0)
        self.assertNotIn("private-hook", str(caught.exception))
        notifier._session.post.assert_called_once()

    def test_non_json_response_is_reported(self):
        notifier = self.notifier()
        notifier._session.post.return_value.json.side_effect = ValueError("invalid")
        with self.assertRaisesRegex(FeishuError, "非 JSON"):
            notifier.send_final_result(task_name="测试", status="failed", duration_ms=0)

    def test_personal_success_remains_compatible(self):
        notifier = FeishuNotifier(FeishuSettings("app", "secret", "ou_test"))
        notifier._request = MagicMock(return_value={})
        notifier.send_final_result(task_name="测试", status="success", duration_ms=0)
        request = notifier._request.call_args
        self.assertEqual(request.kwargs["json_body"]["receive_id"], "ou_test")
        self.assertIn("运行成功", json.loads(request.kwargs["json_body"]["content"])["text"])


if __name__ == "__main__":
    unittest.main()
