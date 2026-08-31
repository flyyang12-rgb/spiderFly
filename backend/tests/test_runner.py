from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app import execution_results, runner
from app.execution_results import ResolvedOutcome
from app.runner import _notification_summary


class NotificationSummaryTests(unittest.TestCase):
    def test_technical_error_takes_priority_over_script_message(self) -> None:
        outcome = ResolvedOutcome(
            status="failed",
            error_message="进程退出码 3",
            result_source="result_json",
            business_outcome="failure",
            result_code="PAGE_REJECTED",
            result_message="页面拒绝了操作",
        )

        self.assertEqual(_notification_summary(outcome), "进程退出码 3")

    def test_script_message_is_used_when_no_error_is_present(self) -> None:
        outcome = ResolvedOutcome(
            status="success",
            error_message="",
            result_source="result_json",
            business_outcome="success",
            result_code="OK",
            result_message="处理完成",
        )

        self.assertEqual(_notification_summary(outcome), "处理完成")

    def test_manual_fields_are_forwarded_to_the_notifier(self) -> None:
        notifier = MagicMock()
        notifier.configured = True
        task = {"name": "账号检查", "notify_on_failure": 1}
        with (
            patch.object(runner, "FeishuNotifier", return_value=notifier),
            patch.object(runner, "execute"),
        ):
            asyncio.run(
                runner._send_notification(
                    12,
                    task,
                    "failed",
                    1500,
                    "登录失效",
                    "LOGIN_EXPIRED",
                    "https://example.com/login",
                    "ACCOUNT_RELOGIN",
                )
            )

        kwargs = notifier.send_final_result.call_args.kwargs
        self.assertEqual(kwargs["result_code"], "LOGIN_EXPIRED")
        self.assertEqual(kwargs["manual_action_url"], "https://example.com/login")
        self.assertEqual(kwargs["manual_code"], "ACCOUNT_RELOGIN")


class RunnerCompatibilityTests(unittest.TestCase):
    def test_managed_template_is_staged_for_the_script_and_cleaned_afterward(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apps_root = root / "apps"
            script_dir = apps_root / "5"
            script_dir.mkdir(parents=True)
            script = script_dir / "main.py"
            script.write_text(
                """
import os
from pathlib import Path

template = Path(os.environ['SPIDERFLY_TEMPLATE_FILE'])
work = Path(os.environ['SPIDERFLY_WORK_DIR'])
print(template.name + ':' + template.read_text(encoding='utf-8'))
(work / 'result.xlsx').write_text('result', encoding='utf-8')
""".strip()
                + "\n",
                encoding="utf-8",
            )
            template = root / "managed-template.xlsx"
            template.write_text("template-v1", encoding="utf-8")
            interpreter = Path(sys.executable).resolve()
            task = {
                "id": 5,
                "name": "模板运行测试",
                "script_path_snapshot": str(script),
                "python_path_snapshot": str(interpreter),
                "template_filename": "财务模板.xlsx",
                "template_path": str(template),
                "timeout_seconds": 600,
                "notify_on_success": 0,
                "notify_on_failure": 0,
            }
            append_output = MagicMock()
            with (
                patch.object(runner, "RPA_APPS_DIR", apps_root),
                patch.object(runner, "RPA_ENVS_DIR", interpreter.parents[1]),
                patch.object(runner, "WORK_DIR", root / "work"),
                patch.object(execution_results, "EXECUTIONS_DIR", root / "executions"),
                patch.object(runner, "fetch_one", return_value=task),
                patch.object(runner, "append_execution_output", append_output),
                patch.object(runner, "execute"),
                patch.object(runner, "_send_notification", new=AsyncMock()),
            ):
                asyncio.run(runner.run_execution(39))

            stdout = "".join(
                call.args[2]
                for call in append_output.call_args_list
                if call.args[1] == "stdout"
            )
            self.assertIn("财务模板.xlsx:template-v1", stdout)
            self.assertEqual(template.read_text(encoding="utf-8"), "template-v1")
            self.assertEqual(tuple((root / "work").iterdir()), ())

    def test_runner_uses_the_platform_timeout_instead_of_task_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apps_root = root / "apps"
            script_dir = apps_root / "6"
            script_dir.mkdir(parents=True)
            script = script_dir / "main.py"
            script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
            interpreter = Path(sys.executable).resolve()
            task = {
                "id": 6,
                "name": "固定超时测试",
                "script_path_snapshot": str(script),
                "python_path_snapshot": str(interpreter),
                "timeout_seconds": 3600,
                "notify_on_success": 0,
                "notify_on_failure": 0,
            }
            execute = MagicMock()
            with (
                patch.object(runner, "RPA_APPS_DIR", apps_root),
                patch.object(runner, "RPA_ENVS_DIR", interpreter.parents[1]),
                patch.object(runner, "WORK_DIR", root / "work"),
                patch.object(runner, "DEFAULT_TASK_TIMEOUT_SECONDS", 0.05),
                patch.object(execution_results, "EXECUTIONS_DIR", root / "executions"),
                patch.object(runner, "fetch_one", return_value=task),
                patch.object(runner, "append_execution_output"),
                patch.object(runner, "execute", execute),
                patch.object(runner, "_send_notification", new=AsyncMock()),
            ):
                asyncio.run(runner.run_execution(40))

            finalize_params = next(
                call.args[1]
                for call in execute.call_args_list
                if "result_source" in call.args[0]
            )
            self.assertEqual(finalize_params[0], "timeout")

    def test_legacy_script_keeps_cwd_and_receives_isolated_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apps_root = root / "apps"
            script_dir = apps_root / "7"
            script_dir.mkdir(parents=True)
            script = script_dir / "main.py"
            script.write_text(
                """
import json
import os

keys = [key for key in os.environ if key.startswith(('SPIDERFLY_', 'FEISHU_'))]
print(json.dumps({'cwd': os.getcwd(), 'environment': {key: os.environ[key] for key in keys}}))
""".strip()
                + "\n",
                encoding="utf-8",
            )
            interpreter = Path(sys.executable).resolve()
            envs_root = interpreter.parents[1]
            task = {
                "id": 7,
                "name": "兼容性测试",
                "script_path_snapshot": str(script),
                "python_path_snapshot": str(interpreter),
                "timeout_seconds": 10,
                "notify_on_success": 0,
                "notify_on_failure": 0,
            }
            append_output = MagicMock()
            execute = MagicMock()
            with (
                patch.object(runner, "RPA_APPS_DIR", apps_root),
                patch.object(runner, "RPA_ENVS_DIR", envs_root),
                patch.object(runner, "WORK_DIR", root / "work"),
                patch.object(execution_results, "EXECUTIONS_DIR", root / "executions"),
                patch.object(runner, "fetch_one", return_value=task),
                patch.object(runner, "append_execution_output", append_output),
                patch.object(runner, "execute", execute),
                patch.object(runner, "_send_notification", new=AsyncMock()),
                patch.dict(
                    os.environ,
                    {
                        "SPIDERFLY_PRIVATE_SECRET": "hidden",
                        "SPIDERFLY_BROWSER_PORT": "9999",
                        "FEISHU_APP_SECRET": "hidden",
                    },
                ),
            ):
                asyncio.run(runner.run_execution(41))

            stdout = "".join(
                call.args[2]
                for call in append_output.call_args_list
                if call.args[1] == "stdout"
            )
            payload = json.loads(stdout.strip())
            environment = payload["environment"]
            self.assertEqual(Path(payload["cwd"]).resolve(), script_dir.resolve())
            self.assertNotIn("SPIDERFLY_PRIVATE_SECRET", environment)
            self.assertFalse(any(key.startswith("FEISHU_") for key in environment))
            expected_keys = {
                "SPIDERFLY_EXECUTION_ID",
                "SPIDERFLY_EXECUTION_DIR",
                "SPIDERFLY_RESULT_FILE",
                "SPIDERFLY_ARTIFACT_DIR",
                "SPIDERFLY_DOWNLOAD_DIR",
                "SPIDERFLY_SCREENSHOT_DIR",
                "SPIDERFLY_TMP_DIR",
                "SPIDERFLY_WORK_DIR",
                "SPIDERFLY_BROWSER_PORT",
                "SPIDERFLY_BROWSER_PROFILE_DIR",
            }
            self.assertEqual(set(environment), expected_keys)
            execution_root = Path(environment["SPIDERFLY_EXECUTION_DIR"]).resolve()
            execution_path_keys = {
                "SPIDERFLY_EXECUTION_DIR",
                "SPIDERFLY_RESULT_FILE",
                "SPIDERFLY_ARTIFACT_DIR",
                "SPIDERFLY_DOWNLOAD_DIR",
                "SPIDERFLY_SCREENSHOT_DIR",
                "SPIDERFLY_TMP_DIR",
            }
            for key in execution_path_keys:
                self.assertTrue(Path(environment[key]).resolve().is_relative_to(execution_root))
            self.assertEqual(Path(environment["SPIDERFLY_WORK_DIR"]).resolve(), (root / "work").resolve())
            self.assertEqual(environment["SPIDERFLY_BROWSER_PORT"], "9123")
            self.assertEqual(
                Path(environment["SPIDERFLY_BROWSER_PROFILE_DIR"]).resolve(),
                (root / "work" / ".spiderfly-browser-9123").resolve(),
            )
            self.assertEqual(tuple((root / "work").iterdir()), ())

            finalize_params = next(
                call.args[1]
                for call in execute.call_args_list
                if "result_source" in call.args[0]
            )
            self.assertEqual(finalize_params[0], "success")
            self.assertEqual(finalize_params[5], "legacy")

    def test_manual_result_flows_from_script_to_database_and_notification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apps_root = root / "apps"
            script_dir = apps_root / "8"
            script_dir.mkdir(parents=True)
            script = script_dir / "main.py"
            script.write_text(
                """
import json
import os
from pathlib import Path

Path(os.environ['SPIDERFLY_RESULT_FILE']).write_text(json.dumps({
    'schema_version': 1,
    'outcome': 'manual_required',
    'code': 'LOGIN_EXPIRED',
    'message': '登录状态失效',
    'retryable': False,
    'manual_action_url': 'https://example.com/login',
    'manual_code': 'ACCOUNT_RELOGIN',
}), encoding='utf-8')
""".strip()
                + "\n",
                encoding="utf-8",
            )
            interpreter = Path(sys.executable).resolve()
            task = {
                "id": 8,
                "name": "人工接管测试",
                "script_path_snapshot": str(script),
                "python_path_snapshot": str(interpreter),
                "timeout_seconds": 10,
                "notify_on_success": 0,
                "notify_on_failure": 1,
            }
            execute = MagicMock()
            notification = AsyncMock()
            with (
                patch.object(runner, "RPA_APPS_DIR", apps_root),
                patch.object(runner, "RPA_ENVS_DIR", interpreter.parents[1]),
                patch.object(runner, "WORK_DIR", root / "work"),
                patch.object(execution_results, "EXECUTIONS_DIR", root / "executions"),
                patch.object(runner, "fetch_one", return_value=task),
                patch.object(runner, "append_execution_output"),
                patch.object(runner, "execute", execute),
                patch.object(runner, "_send_notification", new=notification),
            ):
                asyncio.run(runner.run_execution(42))

            finalize_params = next(
                call.args[1]
                for call in execute.call_args_list
                if "result_source" in call.args[0]
            )
            self.assertEqual(finalize_params[0], "failed")
            self.assertEqual(finalize_params[5:9], (
                "result_json",
                "manual_required",
                "LOGIN_EXPIRED",
                "登录状态失效",
            ))
            self.assertEqual(finalize_params[10:12], (
                "https://example.com/login",
                "ACCOUNT_RELOGIN",
            ))
            notify_args = notification.await_args.args
            self.assertEqual(notify_args[2], "failed")
            self.assertEqual(notify_args[5:], (
                "LOGIN_EXPIRED",
                "https://example.com/login",
                "ACCOUNT_RELOGIN",
            ))


if __name__ == "__main__":
    unittest.main()
