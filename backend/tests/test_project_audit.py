from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app import config, database, main, runner, security
from app.schemas import TaskPatch
from tests import test_app_api as api_helpers


class LogStreamTests(unittest.IsolatedAsyncioTestCase):
    async def check_output(self, data: bytes, expected: str):
        stream = asyncio.StreamReader()
        stream.feed_data(data)
        stream.feed_eof()
        with patch.object(runner, "append_execution_output") as append:
            await runner._consume_stream(stream, 1, "stdout")
        self.assertEqual("".join(call.args[2] for call in append.call_args_list), expected)

    async def test_long_line_does_not_stop_log_consumption(self):
        text = "x" * 200_000 + "\nfinished\n"
        await self.check_output(text.encode(), text)

    async def test_multibyte_characters_cross_chunks_without_corruption(self):
        text = "a" * 16383 + "中文" * 10000 + "\n"
        await self.check_output(text.encode(), text)


class ConfigPathTests(unittest.TestCase):
    def test_relative_paths_are_anchored_to_project_not_launch_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = Path.cwd()
            try:
                os.chdir(root)
                with patch.object(config, "PROJECT_ROOT", root / "project"), patch.dict(
                    os.environ, {"SPIDERFLY_AUDIT_PATH": "data/apps"},
                ):
                    self.assertEqual(config._resolved_path("SPIDERFLY_AUDIT_PATH", root / "fallback"),
                                     root / "project/data/apps")
            finally:
                os.chdir(previous)

    def test_absolute_paths_and_unset_defaults_stay_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            absolute = Path(directory) / "custom"
            with patch.dict(os.environ, {"SPIDERFLY_AUDIT_PATH": str(absolute)}):
                self.assertEqual(config._resolved_path("SPIDERFLY_AUDIT_PATH", Path("unused")), absolute)
            with patch.dict(os.environ):
                os.environ.pop("SPIDERFLY_AUDIT_PATH", None)
                self.assertEqual(config._resolved_path("SPIDERFLY_AUDIT_PATH", absolute), absolute)


class PasswordChangeTests(unittest.TestCase):
    def test_chinese_password_can_be_changed(self):
        current, new = "原密码中文abcdefgh", "新密码中文abcdefgh"
        with patch.object(security, "PASSWORD_ITERATIONS", 1000):
            user = {"id": 1, "username": "audit-user", "password_hash": security._hash_password(current)}
            with patch.object(security, "execute") as execute:
                security.change_password(user, current, new)
            self.assertTrue(security.verify_password(new, execute.call_args.args[1][0]))

    def test_same_chinese_password_is_a_validation_error(self):
        current = "原密码中文abcdefgh"
        with patch.object(security, "PASSWORD_ITERATIONS", 1000):
            user = {"id": 1, "username": "audit-user", "password_hash": security._hash_password(current)}
            with patch.object(security, "execute") as execute:
                with self.assertRaisesRegex(ValueError, "不能与当前密码相同"):
                    security.change_password(user, current, current)
                execute.assert_not_called()


class TaskPatchInputTests(unittest.TestCase):
    def test_explicit_null_is_rejected_but_omitted_fields_are_untouched(self):
        self.assertEqual(TaskPatch().model_dump(exclude_unset=True), {})
        for name in TaskPatch.model_fields:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                TaskPatch.model_validate({name: None})

    def test_update_name_has_same_whitespace_rule_as_creation(self):
        with self.assertRaises(ValidationError):
            TaskPatch(name="   ")
        self.assertEqual(TaskPatch(name="  新名称  ").name, "新名称")


class DisableTaskTests(unittest.TestCase):
    def test_disable_cancels_queue_and_updates_task_status(self):
        helper = api_helpers.ManagedAppApiTests()
        with helper.fixture() as item:
            task = helper.create_task(item["app_id"], item["user"])
            execution_id = main._enqueue_task_sync(task["id"])
            with patch.object(main, "write_audit"):
                updated = main.update_task(task["id"], TaskPatch(enabled=False), object(), item["user"])
            self.assertEqual(database.fetch_one("SELECT status FROM executions WHERE id = ?", (execution_id,))["status"], "cancelled")
            self.assertEqual(updated["last_status"], "cancelled")

    def test_disable_running_task_does_not_cancel_its_process_status(self):
        helper = api_helpers.ManagedAppApiTests()
        with helper.fixture() as item:
            task = helper.create_task(item["app_id"], item["user"])
            execution_id = main._enqueue_task_sync(task["id"])
            database.execute("UPDATE executions SET status = 'running' WHERE id = ?", (execution_id,))
            database.execute("UPDATE tasks SET last_status = 'running' WHERE id = ?", (task["id"],))
            with patch.object(main, "write_audit"):
                updated = main.update_task(task["id"], TaskPatch(enabled=False), object(), item["user"])
            self.assertEqual(updated["last_status"], "running")
            self.assertEqual(database.fetch_one("SELECT status FROM executions WHERE id = ?", (execution_id,))["status"], "running")

    def test_broken_environment_does_not_prevent_disabling_schedule(self):
        helper = api_helpers.ManagedAppApiTests()
        with helper.fixture() as item:
            task = helper.create_task(item["app_id"], item["user"])
            database.execute("UPDATE rpa_apps SET env_path = '', environment_status = 'failed' WHERE id = ?", (item["app_id"],))
            with patch.object(main, "write_audit"):
                updated = main.update_task(task["id"], TaskPatch(enabled=False), object(), item["user"])
            self.assertFalse(updated["enabled"])
            self.assertIsNone(updated["next_run_at"])


if __name__ == "__main__":
    unittest.main()
