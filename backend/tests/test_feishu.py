from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from app.feishu import FeishuNotifier, FeishuSettings


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


if __name__ == "__main__":
    unittest.main()
