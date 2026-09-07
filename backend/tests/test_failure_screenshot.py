from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image
from app import runner, execution_results
from app.feishu import FeishuNotifier, FeishuSettings


class FailureScreenshotTests(unittest.TestCase):
    def run_script(self, source, *, enabled=True, notify=True, capture_ok=True, timeout=10):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scripts = root / "apps"
            scripts.mkdir()
            script = scripts / "main.py"
            script.write_text(source, encoding="utf-8")
            output = {"stdout": "", "stderr": ""}
            task = dict(id=1, name="合成截图测试", script_path_snapshot=str(script),
                        python_path_snapshot=sys.executable, failure_screenshot=enabled,
                        notify_on_failure=notify, notify_on_success=False)
            order = []
            buf = io.BytesIO()
            Image.new("RGB", (16, 16), "green").save(buf, format="JPEG")
            jpeg = buf.getvalue()
            def capture():
                order.append("capture")
                return jpeg if capture_ok else None
            def cleanup(*args):
                order.append("cleanup")
            original_terminate = runner._terminate_process
            async def terminate(process):
                order.append("terminate")
                await original_terminate(process)
            def append(execution_id, field, value):
                output[field] += value
            with (
                patch.object(runner, "RPA_APPS_DIR", scripts),
                patch.object(runner, "RPA_ENVS_DIR", Path(sys.executable).resolve().parents[1]),
                patch.object(runner, "WORK_DIR", root / "work"),
                patch.object(execution_results, "EXECUTIONS_DIR", root / "executions"),
                patch.object(runner, "fetch_one", side_effect=lambda sql, params: output if "SELECT stdout" in sql else task),
                patch.object(runner, "append_execution_output", side_effect=append),
                patch.object(runner, "execute"),
                patch.object(runner, "_finalize", new=AsyncMock()) as finalize,
                patch.object(runner, "_send_notification", new=AsyncMock()) as send,
                patch.object(runner, "capture_active_window_jpeg", side_effect=capture),
                patch.object(runner, "cleanup_after_run", side_effect=cleanup),
                patch.object(runner, "_terminate_process", side_effect=terminate),
                patch.object(runner, "DEFAULT_TASK_TIMEOUT_SECONDS", timeout),
            ):
                asyncio.run(runner.run_execution(1))
            saved = [p.read_bytes() for p in (root / "executions/1/artifacts").glob("failure-*.jpg")]
            return finalize.call_args.args[2], send.call_args, saved, output, order, jpeg

    def test_failure_captures_before_cleanup_and_preserves_traceback(self):
        outcome, send, saved, output, order, jpeg = self.run_script("raise ValueError('browser click failed')")
        self.assertEqual(outcome.status, "failed")
        self.assertIn("ValueError: browser click failed", outcome.error_message)
        self.assertIn("Traceback", output["stderr"])
        self.assertEqual(saved, [jpeg])
        self.assertEqual(send.kwargs["image_bytes"], jpeg)
        self.assertLess(order.index("capture"), order.index("cleanup"))

    def test_disabled_notification_or_capture_keeps_logs_without_image(self):
        for enabled, notify in [(False, True), (True, False)]:
            with self.subTest(enabled=enabled, notify=notify):
                outcome, send, saved, output, order, _ = self.run_script("print(undefined_name)", enabled=enabled, notify=notify)
                self.assertEqual(outcome.status, "failed")
                self.assertIn("NameError", output["stderr"])
                self.assertEqual(saved, [])
                self.assertIsNone(send.kwargs["image_bytes"])
                self.assertNotIn("capture", order)

    def test_success_never_captures(self):
        outcome, send, saved, _, order, _ = self.run_script("print('ok')")
        self.assertEqual(outcome.status, "success")
        self.assertEqual(saved, [])
        self.assertNotIn("capture", order)

    def test_capture_failure_does_not_replace_original_error(self):
        outcome, send, saved, output, _, _ = self.run_script("raise RuntimeError('original')", capture_ok=False)
        self.assertIn("RuntimeError: original", outcome.error_message)
        self.assertIn("未能获取", send.kwargs["screenshot_note"])
        self.assertEqual(saved, [])
        self.assertIn("RuntimeError: original", output["stderr"])

    def test_timeout_captures_once_before_termination(self):
        outcome, _, saved, _, order, _ = self.run_script("import time; time.sleep(10)", timeout=.1)
        self.assertEqual(outcome.status, "timeout")
        self.assertEqual(order.count("capture"), 1)
        self.assertLess(order.index("capture"), order.index("terminate"))
        self.assertEqual(len(saved), 1)

    def test_business_failure_with_zero_exit_captures(self):
        source = "import json, os; from pathlib import Path; Path(os.environ['SPIDERFLY_RESULT_FILE']).write_text(json.dumps({'schema_version': 1, 'outcome': 'failure', 'code': 'CLICK_FAILED', 'message': 'click failed'}))"
        outcome, _, saved, _, order, _ = self.run_script(source)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result_code, "CLICK_FAILED")
        self.assertEqual(len(saved), 1)
        self.assertLess(order.index("capture"), order.index("cleanup"))

    def test_notification_forwards_image_and_reason(self):
        notifier = MagicMock(configured=True)
        with patch.object(runner, "FeishuNotifier", return_value=notifier), patch.object(runner, "execute"):
            asyncio.run(runner._send_notification(1, {"name": "test", "notify_on_failure": True}, "failed", 100, "original", image_bytes=b"jpeg"))
        self.assertEqual(notifier.send_final_result.call_args.kwargs["image_bytes"], b"jpeg")
        self.assertEqual(notifier.send_final_result.call_args.kwargs["error_summary"], "original")


class ScreenshotDeliveryTests(unittest.TestCase):
    def notifier(self, *, webhook=True, credentials=True):
        settings = FeishuSettings("app" if credentials else "", "secret" if credentials else "", "ou_test", webhook_url="https://example.com/hook" if webhook else "")
        notifier = FeishuNotifier(settings)
        notifier._upload_image = MagicMock(return_value="img_test")
        notifier._send_message = MagicMock()
        return notifier

    def test_personal_and_group_attach_image_to_single_failure_post(self):
        for webhook in (True, False):
            with self.subTest(webhook=webhook):
                notifier = self.notifier(webhook=webhook)
                notifier.send_final_result(task_name="task", status="failed", duration_ms=1, error_summary="NameError: name", image_bytes=b"jpeg")
                notifier._send_message.assert_called_once()
                rows = notifier._send_message.call_args.args[2]["zh_cn"]["content"]
                self.assertIn([{"tag": "img", "image_key": "img_test"}], rows)
                self.assertIn("NameError: name", json.dumps(rows))
                self.assertEqual(notifier._send_message.call_args.args[0], "" if webhook else "ou_test")

    def test_upload_failure_and_missing_credentials_keep_error_notification(self):
        for credentials in (True, False):
            notifier = self.notifier(credentials=credentials)
            notifier._upload_image.side_effect = RuntimeError("private-secret")
            notifier.send_final_result(task_name="task", status="failed", duration_ms=1, error_summary="original", image_bytes=b"jpeg")
            notifier._send_message.assert_called_once()
            content = json.dumps(notifier._send_message.call_args.args[2], ensure_ascii=False)
            self.assertIn("original", content)
            self.assertNotIn("private-secret", content)
            self.assertNotIn('"tag": "img"', content)
            self.assertIn("上传失败" if credentials else "App ID", content)
            if not credentials:
                notifier._upload_image.assert_not_called()

    def test_long_traceback_keeps_exception_tail(self):
        notifier = self.notifier()
        notifier.send_final_result(task_name="task", status="failed", duration_ms=1, error_summary="frame\n" * 300 + "NameError: missing")
        self.assertIn("NameError: missing", json.dumps(notifier._send_message.call_args.args[2]))
