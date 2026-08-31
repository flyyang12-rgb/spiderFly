from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app import database, environments, execution_results


class LegacyTaskProgramCleanupTests(unittest.TestCase):
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
                    ) VALUES ('cleanup-admin', '清理测试', 'unused', 'admin', 1, 0, ?, ?)
                    """,
                    (now, now),
                )
                yield {
                    "data_dir": data_dir,
                    "apps_root": apps_root,
                    "envs_root": envs_root,
                    "executions_root": executions_root,
                    "user_id": user_id,
                }

    @staticmethod
    def publish_fake_environment(envs_root: Path, app_id: int) -> Path:
        env_dir = envs_root / f"app_{app_id}_r1_1234abcd"
        env_dir.mkdir(parents=True)
        database.execute(
            "UPDATE rpa_apps SET env_path = ?, environment_status = 'ready' WHERE id = ?",
            (str(env_dir), app_id),
        )
        return env_dir

    @staticmethod
    def insert_archived_task(app: dict, name: str) -> int:
        now = database.utc_now()
        return database.execute(
            """
            INSERT INTO tasks (
                name, app_id, app_name, script_path, python_path,
                enabled, archived, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '', 0, 1, ?, ?)
            """,
            (name, app["id"], app["name"], app["script_path"], now, now),
        )

    @staticmethod
    def insert_execution(task_id: int, status: str = "success") -> int:
        now = database.utc_now()
        return database.execute(
            "INSERT INTO executions (task_id, status, created_at) VALUES (?, ?, ?)",
            (task_id, status, now),
        )

    def test_cleanup_removes_only_archived_tasks_and_orphan_apps(self) -> None:
        with self.fixture() as item:
            active = environments.create_managed_task_bundle(
                "保留任务", "active.py", b"print('active')\n", "", item["user_id"]
            )
            active_app_id = int(active["id"])
            active_task_id = int(active["task_id"])
            active_env = self.publish_fake_environment(
                item["envs_root"], active_app_id
            )
            active_execution_id = self.insert_execution(active_task_id)
            active_workspace = execution_results.create_execution_workspace(
                active_execution_id
            )

            shared_archived_task_id = self.insert_archived_task(
                active, "保留程序的旧任务"
            )
            shared_execution_id = self.insert_execution(shared_archived_task_id)
            shared_workspace = execution_results.create_execution_workspace(
                shared_execution_id
            )

            orphan = environments.create_managed_app(
                "遗留程序", "orphan.py", b"print('orphan')\n", "", item["user_id"]
            )
            orphan_app_id = int(orphan["id"])
            orphan_env = self.publish_fake_environment(
                item["envs_root"], orphan_app_id
            )
            orphan_task_id = self.insert_archived_task(orphan, "遗留归档任务")
            orphan_execution_id = self.insert_execution(orphan_task_id)
            orphan_workspace = execution_results.create_execution_workspace(
                orphan_execution_id
            )

            archived_orphan = environments.create_managed_app(
                "已归档孤立程序",
                "archived.py",
                b"print('archived')\n",
                "",
                item["user_id"],
            )
            archived_orphan_id = int(archived_orphan["id"])
            archived_orphan_env = self.publish_fake_environment(
                item["envs_root"], archived_orphan_id
            )
            database.execute(
                "UPDATE rpa_apps SET archived = 1, archived_at = ? WHERE id = ?",
                (database.utc_now(), archived_orphan_id),
            )

            result = environments.cleanup_legacy_task_program_model()
            marker_path, backup_path = environments._legacy_cleanup_paths()

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["deleted_task_count"], 2)
            self.assertEqual(result["deleted_app_count"], 2)
            self.assertTrue(marker_path.is_file())
            self.assertTrue(backup_path.is_file())

            self.assertIsNotNone(
                database.fetch_one("SELECT id FROM tasks WHERE id = ?", (active_task_id,))
            )
            self.assertIsNotNone(
                database.fetch_one("SELECT id FROM rpa_apps WHERE id = ?", (active_app_id,))
            )
            self.assertIsNotNone(
                database.fetch_one(
                    "SELECT id FROM executions WHERE id = ?", (active_execution_id,)
                )
            )
            self.assertTrue(Path(active["script_path"]).is_file())
            self.assertTrue(active_env.is_dir())
            self.assertTrue(active_workspace.root.is_dir())

            for task_id in (shared_archived_task_id, orphan_task_id):
                self.assertIsNone(
                    database.fetch_one("SELECT id FROM tasks WHERE id = ?", (task_id,))
                )
            for app_id in (orphan_app_id, archived_orphan_id):
                self.assertIsNone(
                    database.fetch_one("SELECT id FROM rpa_apps WHERE id = ?", (app_id,))
                )
            self.assertFalse(Path(orphan["script_path"]).parent.exists())
            self.assertFalse(orphan_env.exists())
            self.assertFalse(Path(archived_orphan["script_path"]).parent.exists())
            self.assertFalse(archived_orphan_env.exists())
            self.assertFalse(shared_workspace.root.exists())
            self.assertFalse(orphan_workspace.root.exists())

            second = environments.cleanup_legacy_task_program_model()
            self.assertEqual(second["status"], "already_complete")
            self.assertEqual(
                database.fetch_one("SELECT COUNT(*) AS total FROM tasks")["total"], 1
            )
            self.assertEqual(
                database.fetch_one("SELECT COUNT(*) AS total FROM rpa_apps")["total"], 1
            )

    def test_running_execution_fails_closed_before_backup_or_file_changes(self) -> None:
        with self.fixture() as item:
            orphan = environments.create_managed_app(
                "运行中的旧程序",
                "running.py",
                b"print('running')\n",
                "",
                item["user_id"],
            )
            app_id = int(orphan["id"])
            env_dir = self.publish_fake_environment(item["envs_root"], app_id)
            task_id = self.insert_archived_task(orphan, "运行中的归档任务")
            execution_id = self.insert_execution(task_id, "running")
            workspace = execution_results.create_execution_workspace(execution_id)

            with self.assertRaisesRegex(RuntimeError, "仍在运行"):
                environments.cleanup_legacy_task_program_model()

            marker_path, backup_path = environments._legacy_cleanup_paths()
            self.assertFalse(marker_path.exists())
            self.assertFalse(backup_path.exists())
            self.assertIsNotNone(
                database.fetch_one("SELECT id FROM tasks WHERE id = ?", (task_id,))
            )
            self.assertIsNotNone(
                database.fetch_one("SELECT id FROM rpa_apps WHERE id = ?", (app_id,))
            )
            self.assertTrue(Path(orphan["script_path"]).is_file())
            self.assertTrue(env_dir.is_dir())
            self.assertTrue(workspace.root.is_dir())

    def test_invalid_active_task_shapes_fail_closed(self) -> None:
        with self.subTest(case="missing_app"):
            with self.fixture() as item:
                now = database.utc_now()
                task_id = database.execute(
                    """
                    INSERT INTO tasks (
                        name, app_id, app_name, script_path, python_path,
                        enabled, archived, created_at, updated_at
                    ) VALUES ('缺程序', NULL, '', 'missing.py', '', 1, 0, ?, ?)
                    """,
                    (now, now),
                )
                with self.assertRaisesRegex(RuntimeError, "缺少对应程序"):
                    environments.cleanup_legacy_task_program_model()
                self.assertIsNotNone(
                    database.fetch_one("SELECT id FROM tasks WHERE id = ?", (task_id,))
                )
                self.assertFalse(environments._legacy_cleanup_paths()[0].exists())

        with self.subTest(case="archived_app"):
            with self.fixture() as item:
                active = environments.create_managed_task_bundle(
                    "错误归档", "main.py", b"pass\n", "", item["user_id"]
                )
                database.execute(
                    "UPDATE rpa_apps SET archived = 1 WHERE id = ?", (active["id"],)
                )
                with self.assertRaisesRegex(RuntimeError, "对应程序已归档"):
                    environments.cleanup_legacy_task_program_model()
                self.assertIsNotNone(
                    database.fetch_one(
                        "SELECT id FROM tasks WHERE id = ?", (active["task_id"],)
                    )
                )
                self.assertTrue(Path(active["script_path"]).is_file())
                self.assertFalse(environments._legacy_cleanup_paths()[0].exists())

        with self.subTest(case="duplicate_active_tasks"):
            with self.fixture() as item:
                active = environments.create_managed_task_bundle(
                    "重复绑定", "main.py", b"pass\n", "", item["user_id"]
                )
                database.execute("DROP INDEX uq_tasks_one_active_app")
                now = database.utc_now()
                second_task_id = database.execute(
                    """
                    INSERT INTO tasks (
                        name, app_id, app_name, script_path, python_path,
                        enabled, archived, created_at, updated_at
                    ) VALUES ('重复绑定二', ?, ?, ?, '', 1, 0, ?, ?)
                    """,
                    (
                        active["id"],
                        active["name"],
                        active["script_path"],
                        now,
                        now,
                    ),
                )
                with self.assertRaisesRegex(RuntimeError, "绑定 2 个有效任务"):
                    environments.cleanup_legacy_task_program_model()
                self.assertIsNotNone(
                    database.fetch_one(
                        "SELECT id FROM tasks WHERE id = ?", (second_task_id,)
                    )
                )
                self.assertTrue(Path(active["script_path"]).is_file())
                self.assertFalse(environments._legacy_cleanup_paths()[0].exists())

    def test_filesystem_failure_leaves_database_rows_and_marker_untouched(self) -> None:
        with self.fixture() as item:
            orphan = environments.create_managed_app(
                "无法清理", "main.py", b"pass\n", "", item["user_id"]
            )
            task_id = self.insert_archived_task(orphan, "无法清理的归档任务")

            with (
                patch.object(
                    environments,
                    "remove_managed_app_storage",
                    side_effect=RuntimeError("locked"),
                ),
                self.assertRaisesRegex(RuntimeError, "locked"),
            ):
                environments.cleanup_legacy_task_program_model()

            marker_path, backup_path = environments._legacy_cleanup_paths()
            self.assertFalse(marker_path.exists())
            self.assertTrue(backup_path.is_file())
            self.assertIsNotNone(
                database.fetch_one("SELECT id FROM tasks WHERE id = ?", (task_id,))
            )
            self.assertIsNotNone(
                database.fetch_one("SELECT id FROM rpa_apps WHERE id = ?", (orphan["id"],))
            )
            self.assertTrue(Path(orphan["script_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
