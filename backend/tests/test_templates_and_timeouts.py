from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import database, environments
from app.schemas import TaskPatch, TaskPayload


class TemplateAndTimeoutTests(unittest.TestCase):
    def test_zero_timeout_is_normalized_to_ten_minutes(self) -> None:
        payload = TaskPayload(name="对账", app_id=1, timeout_seconds=0)
        patch_payload = TaskPatch(timeout_seconds=1800)

        self.assertEqual(payload.timeout_seconds, 600)
        self.assertEqual(patch_payload.timeout_seconds, 600)

    def test_existing_zero_timeout_and_app_table_are_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            data_dir.mkdir()
            db_path = data_dir / "spiderfly.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.executescript(
                    """
                    CREATE TABLE rpa_apps (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        script_filename TEXT NOT NULL DEFAULT 'main.py',
                        script_path TEXT NOT NULL DEFAULT '',
                        requirements_text TEXT NOT NULL DEFAULT '',
                        env_path TEXT NOT NULL DEFAULT '',
                        environment_status TEXT NOT NULL DEFAULT 'pending',
                        environment_error TEXT NOT NULL DEFAULT '',
                        install_log TEXT NOT NULL DEFAULT '',
                        revision INTEGER NOT NULL DEFAULT 1,
                        legacy_source_path TEXT NOT NULL DEFAULT '',
                        created_by INTEGER,
                        updated_by INTEGER,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        description TEXT NOT NULL DEFAULT '',
                        script_path TEXT NOT NULL,
                        python_path TEXT NOT NULL DEFAULT '',
                        enabled INTEGER NOT NULL DEFAULT 1,
                        timeout_seconds INTEGER NOT NULL DEFAULT 0,
                        notify_on_success INTEGER NOT NULL DEFAULT 1,
                        notify_on_failure INTEGER NOT NULL DEFAULT 1,
                        last_status TEXT NOT NULL DEFAULT 'idle',
                        last_run_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    INSERT INTO tasks (name, script_path, timeout_seconds, created_at, updated_at)
                    VALUES ('旧任务', 'missing.py', 0, '2026-01-01', '2026-01-01');
                    INSERT INTO tasks (name, script_path, timeout_seconds, created_at, updated_at)
                    VALUES ('旧长任务', 'missing.py', 1800, '2026-01-01', '2026-01-01');
                    """
                )
                conn.commit()

            with (
                patch.object(database, "DATA_DIR", data_dir),
                patch.object(database, "DB_PATH", db_path),
                patch.object(database, "RPA_APPS_DIR", data_dir / "apps"),
                patch.object(database, "RPA_ENVS_DIR", data_dir / "envs"),
            ):
                database.init_db()

            with closing(sqlite3.connect(db_path)) as conn:
                timeouts = {
                    row[0]
                    for row in conn.execute(
                        "SELECT timeout_seconds FROM tasks WHERE name IN ('旧任务', '旧长任务')"
                    )
                }
                columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(rpa_apps)")
                }

        self.assertEqual(timeouts, {600})
        self.assertTrue({"template_filename", "template_path"}.issubset(columns))

    def test_excel_template_is_saved_with_the_managed_app(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            apps_root = data_dir / "apps"
            envs_root = data_dir / "envs"
            db_path = data_dir / "spiderfly.db"
            with (
                patch.object(database, "DATA_DIR", data_dir),
                patch.object(database, "DB_PATH", db_path),
                patch.object(database, "RPA_APPS_DIR", apps_root),
                patch.object(database, "RPA_ENVS_DIR", envs_root),
                patch.object(environments, "RPA_APPS_DIR", apps_root),
                patch.object(environments, "RPA_ENVS_DIR", envs_root),
            ):
                database.init_db()
                now = database.utc_now()
                user_id = database.execute(
                    """
                    INSERT INTO users (
                        username, display_name, password_hash, role, active,
                        must_change_password, created_at, updated_at
                    ) VALUES ('template-admin', '模板管理员', 'unused', 'admin', 1, 0, ?, ?)
                    """,
                    (now, now),
                )
                created = environments.create_managed_app(
                    "模板程序",
                    "main.py",
                    b"print('ok')\n",
                    "",
                    user_id,
                    "月报模板.xlsx",
                    b"excel-template-bytes",
                )

                template_path = Path(created["template_path"])
                self.assertEqual(created["template_filename"], "月报模板.xlsx")
                self.assertEqual(template_path.read_bytes(), b"excel-template-bytes")
                self.assertEqual(template_path.parent, apps_root / str(created["id"]))

    def test_template_rejects_paths_and_non_excel_files(self) -> None:
        with self.assertRaisesRegex(ValueError, "Excel 模板"):
            environments.validate_template_upload("../模板.xlsx", b"x")
        with self.assertRaisesRegex(ValueError, "Excel 模板"):
            environments.validate_template_upload("模板.csv", b"x")


if __name__ == "__main__":
    unittest.main()
