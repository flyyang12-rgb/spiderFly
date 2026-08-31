from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app import database, environments, execution_results, main
from app.schemas import TaskPayload


class ManagedAppApiTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            apps_root = data_dir / "apps"
            envs_root = data_dir / "envs"
            executions_root = data_dir / "executions"
            db_path = data_dir / "spiderfly.db"
            with (
                patch.object(database, "DATA_DIR", data_dir),
                patch.object(database, "DB_PATH", db_path),
                patch.object(database, "RPA_APPS_DIR", apps_root),
                patch.object(database, "RPA_ENVS_DIR", envs_root),
                patch.object(environments, "RPA_APPS_DIR", apps_root),
                patch.object(environments, "RPA_ENVS_DIR", envs_root),
                patch.object(execution_results, "EXECUTIONS_DIR", executions_root),
            ):
                database.init_db()
                now = database.utc_now()
                user_id = database.execute(
                    """
                    INSERT INTO users (
                        username, display_name, password_hash, role, active,
                        must_change_password, created_at, updated_at
                    ) VALUES ('api-admin', '接口管理员', 'not-used', 'admin', 1, 0, ?, ?)
                    """,
                    (now, now),
                )
                created = environments.create_managed_app(
                    "测试程序",
                    "main.py",
                    b"print('ok')\n",
                    "requests==2.32.5",
                    user_id,
                    "template.xlsx",
                    b"excel-template",
                )
                app_id = int(created["id"])
                env_dir = envs_root / f"app_{app_id}_r1_1234abcd"
                python_path = environments._environment_python(env_dir)
                python_path.parent.mkdir(parents=True)
                python_path.write_bytes(b"python")
                database.execute(
                    "UPDATE rpa_apps SET env_path = ?, environment_status = 'ready' WHERE id = ?",
                    (str(env_dir), app_id),
                )
                user = {
                    "id": user_id,
                    "username": "api-admin",
                    "display_name": "接口管理员",
                    "role": "admin",
                    "must_change_password": 0,
                }
                yield {
                    "app_id": app_id,
                    "app_dir": apps_root / str(app_id),
                    "env_dir": env_dir,
                    "user": user,
                }

    def create_task(self, app_id: int, user: dict, name: str = "每天对账") -> dict:
        payload = TaskPayload(name=name, app_id=app_id, trigger_type="manual")
        with patch.object(main, "write_audit"):
            return main.create_task(payload, request=object(), user=user)

    def test_one_app_can_only_bind_one_task(self) -> None:
        with self.fixture() as item:
            self.create_task(item["app_id"], item["user"])
            with self.assertRaises(HTTPException) as caught:
                self.create_task(item["app_id"], item["user"], "第二个任务")
            self.assertEqual(caught.exception.status_code, 409)
            self.assertIn("已经绑定任务", caught.exception.detail)
            self.assertEqual(
                database.fetch_one("SELECT COUNT(*) AS total FROM tasks")["total"], 1
            )

    def test_upload_atomically_creates_pending_app_and_manual_task(self) -> None:
        with self.fixture() as item:
            script = UploadFile(
                filename="daily_report.py",
                file=io.BytesIO(b"print('daily report')\n"),
            )
            with patch.object(main, "write_audit") as audit_write:
                created = asyncio.run(
                    main.create_app(
                        request=object(),
                        name="财务日报",
                        requirements_text="openpyxl==3.1.5",
                        script=script,
                        template=None,
                        user=item["user"],
                    )
                )

            self.assertEqual(created["name"], "财务日报")
            self.assertEqual(created["environment_status"], "pending")
            self.assertEqual(created["active_task_count"], 1)
            self.assertEqual(created["task"]["name"], "财务日报")
            self.assertEqual(created["task"]["trigger_type"], "manual")
            self.assertTrue(created["task"]["enabled"])
            self.assertFalse(created["task"]["runtime_ready"])
            stored = database.fetch_one(
                "SELECT app_id, python_path, trigger_type FROM tasks WHERE id = ?",
                (created["task"]["id"],),
            )
            self.assertEqual(stored["app_id"], created["id"])
            self.assertEqual(stored["python_path"], "")
            self.assertEqual(stored["trigger_type"], "manual")
            self.assertTrue((item["app_dir"].parent / str(created["id"]) / "daily_report.py").is_file())
            audit_write.assert_called_once()
            self.assertEqual(audit_write.call_args.args[2], "create_task")

    def test_upload_saves_complete_daily_task_settings(self) -> None:
        with self.fixture() as item:
            script = UploadFile(
                filename="daily.py",
                file=io.BytesIO(b"print('daily')\n"),
            )
            with patch.object(main, "write_audit"):
                created = asyncio.run(
                    main.create_app(
                        request=object(),
                        name="每日财务任务",
                        requirements_text="",
                        script=script,
                        template=None,
                        description="  每天汇总财务数据  ",
                        trigger_type="daily",
                        trigger_config=json.dumps({"time": "09:05"}),
                        enabled=True,
                        notify_on_success=False,
                        notify_on_failure=True,
                        user=item["user"],
                    )
                )

            task = created["task"]
            self.assertEqual(task["description"], "每天汇总财务数据")
            self.assertEqual(task["trigger_type"], "daily")
            self.assertEqual(task["trigger_config"], {"time": "09:05"})
            self.assertIsNotNone(task["next_run_at"])
            self.assertTrue(task["enabled"])
            self.assertFalse(task["notify_on_success"])
            self.assertTrue(task["notify_on_failure"])

    def test_upload_saves_complete_weekly_task_settings(self) -> None:
        with self.fixture() as item:
            script = UploadFile(
                filename="weekly.py",
                file=io.BytesIO(b"print('weekly')\n"),
            )
            with patch.object(main, "write_audit"):
                created = asyncio.run(
                    main.create_app(
                        request=object(),
                        name="每周财务任务",
                        requirements_text="",
                        script=script,
                        template=None,
                        description="每周一和周五处理",
                        trigger_type="weekly",
                        trigger_config=json.dumps(
                            {"time": "18:30", "weekdays": [5, 1, 5]}
                        ),
                        enabled=False,
                        notify_on_success=True,
                        notify_on_failure=False,
                        user=item["user"],
                    )
                )

            task = created["task"]
            self.assertEqual(task["description"], "每周一和周五处理")
            self.assertEqual(task["trigger_type"], "weekly")
            self.assertEqual(
                task["trigger_config"], {"weekdays": [1, 5], "time": "18:30"}
            )
            self.assertIsNone(task["next_run_at"])
            self.assertFalse(task["enabled"])
            self.assertTrue(task["notify_on_success"])
            self.assertFalse(task["notify_on_failure"])

    def test_invalid_upload_schedule_leaves_no_program_task_or_files(self) -> None:
        with self.fixture() as item:
            original_apps = database.fetch_one(
                "SELECT COUNT(*) AS total FROM rpa_apps"
            )["total"]
            original_tasks = database.fetch_one(
                "SELECT COUNT(*) AS total FROM tasks"
            )["total"]
            original_dirs = {path.name for path in item["app_dir"].parent.iterdir()}
            script = UploadFile(
                filename="invalid_schedule.py",
                file=io.BytesIO(b"print('invalid')\n"),
            )

            with self.assertRaises(HTTPException) as caught:
                asyncio.run(
                    main.create_app(
                        request=object(),
                        name="错误日程任务",
                        requirements_text="",
                        script=script,
                        template=None,
                        description="不应保存",
                        trigger_type="weekly",
                        trigger_config=json.dumps(
                            {"time": "09:00", "weekdays": []}
                        ),
                        enabled=True,
                        notify_on_success=True,
                        notify_on_failure=True,
                        user=item["user"],
                    )
                )

            self.assertEqual(caught.exception.status_code, 400)
            self.assertIn("至少选择一个星期", caught.exception.detail)
            self.assertEqual(
                database.fetch_one("SELECT COUNT(*) AS total FROM rpa_apps")[
                    "total"
                ],
                original_apps,
            )
            self.assertEqual(
                database.fetch_one("SELECT COUNT(*) AS total FROM tasks")["total"],
                original_tasks,
            )
            self.assertEqual(
                {path.name for path in item["app_dir"].parent.iterdir()},
                original_dirs,
            )

    def test_bundle_rolls_back_program_files_when_task_name_conflicts(self) -> None:
        with self.fixture() as item:
            self.create_task(item["app_id"], item["user"], name="重复任务")
            original_dirs = {path.name for path in item["app_dir"].parent.iterdir()}

            with self.assertRaises(sqlite3.IntegrityError):
                environments.create_managed_task_bundle(
                    "重复任务",
                    "duplicate.py",
                    b"print('duplicate')\n",
                    "",
                    item["user"]["id"],
                )

            self.assertIsNone(
                database.fetch_one(
                    "SELECT id FROM rpa_apps WHERE name = ?", ("重复任务",)
                )
            )
            self.assertEqual(
                {path.name for path in item["app_dir"].parent.iterdir()}, original_dirs
            )
            self.assertEqual(
                database.fetch_one("SELECT COUNT(*) AS total FROM tasks")["total"], 1
            )

    def test_deleting_task_hard_deletes_app_history_and_managed_files(self) -> None:
        with self.fixture() as item:
            task = self.create_task(item["app_id"], item["user"])
            now = database.utc_now()
            execution_id = database.execute(
                """
                INSERT INTO executions (task_id, status, exit_code, created_at, ended_at)
                VALUES (?, 'success', 0, ?, ?)
                """,
                (task["id"], now, now),
            )
            workspace = execution_results.create_execution_workspace(execution_id)
            (workspace.artifacts_dir / "result.txt").write_text("ok", encoding="utf-8")

            with patch.object(main, "write_audit") as audit_write:
                main.delete_task(task["id"], request=object(), user=item["user"])

            self.assertIsNone(database.fetch_one("SELECT id FROM tasks WHERE id = ?", (task["id"],)))
            self.assertIsNone(database.fetch_one("SELECT id FROM executions WHERE id = ?", (execution_id,)))
            self.assertIsNone(database.fetch_one("SELECT id FROM rpa_apps WHERE id = ?", (item["app_id"],)))
            self.assertFalse(item["app_dir"].exists())
            self.assertFalse(item["env_dir"].exists())
            self.assertFalse(workspace.root.exists())
            audit_write.assert_called_once()
            self.assertEqual(audit_write.call_args.args[2], "delete_task")

    def test_running_task_cannot_be_deleted(self) -> None:
        with self.fixture() as item:
            task = self.create_task(item["app_id"], item["user"])
            database.execute(
                "INSERT INTO executions (task_id, status, created_at) VALUES (?, 'running', ?)",
                (task["id"], database.utc_now()),
            )
            with self.assertRaises(HTTPException) as caught:
                main.delete_task(task["id"], request=object(), user=item["user"])
            self.assertEqual(caught.exception.status_code, 409)
            self.assertIn("正在运行", caught.exception.detail)
            self.assertIsNotNone(database.fetch_one("SELECT id FROM tasks WHERE id = ?", (task["id"],)))
            self.assertIsNotNone(database.fetch_one("SELECT id FROM rpa_apps WHERE id = ?", (item["app_id"],)))
            self.assertTrue(item["app_dir"].is_dir())
            self.assertTrue(item["env_dir"].is_dir())

    def test_deleting_unbound_app_removes_row_source_and_venv(self) -> None:
        with self.fixture() as item:
            with patch.object(main, "write_audit") as audit_write:
                removed = main.delete_app(
                    item["app_id"], request=object(), user=item["user"]
                )
            self.assertEqual(removed["name"], "测试程序")
            self.assertIsNone(
                database.fetch_one("SELECT id FROM rpa_apps WHERE id = ?", (item["app_id"],))
            )
            self.assertFalse(item["app_dir"].exists())
            self.assertFalse(item["env_dir"].exists())
            self.assertEqual(audit_write.call_args.args[2], "delete_app")


if __name__ == "__main__":
    unittest.main()
