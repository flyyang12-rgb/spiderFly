from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import database


class ExecutionResultMigrationTests(unittest.TestCase):
    def test_existing_execution_rows_receive_safe_structured_result_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            db_path = data_dir / "spiderfly.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE executions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        trigger_source TEXT NOT NULL DEFAULT 'manual',
                        requested_by INTEGER,
                        script_path_snapshot TEXT NOT NULL DEFAULT '',
                        python_path_snapshot TEXT NOT NULL DEFAULT '',
                        started_at TEXT, ended_at TEXT, duration_ms INTEGER, exit_code INTEGER,
                        stdout TEXT NOT NULL DEFAULT '', stderr TEXT NOT NULL DEFAULT '',
                        error_message TEXT NOT NULL DEFAULT '',
                        notification_status TEXT NOT NULL DEFAULT 'pending',
                        notification_error TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO executions (task_id, status, created_at) VALUES (1, 'success', '2026-01-01T00:00:00+00:00')"
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
                row = conn.execute(
                    "SELECT result_source, business_outcome, result_code, result_message, retryable, manual_action_url, manual_code FROM executions WHERE id = 1"
                ).fetchone()

        self.assertEqual(row, ("legacy", "", "", "", None, "", ""))

    def test_fresh_database_contains_structured_result_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            db_path = data_dir / "spiderfly.db"
            with (
                patch.object(database, "DATA_DIR", data_dir),
                patch.object(database, "DB_PATH", db_path),
                patch.object(database, "RPA_APPS_DIR", data_dir / "apps"),
                patch.object(database, "RPA_ENVS_DIR", data_dir / "envs"),
            ):
                database.init_db()
                database.init_db()

            with closing(sqlite3.connect(db_path)) as conn:
                columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(executions)")
                }
                task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
                app_count = conn.execute("SELECT COUNT(*) FROM rpa_apps").fetchone()[0]

        self.assertTrue(
            {
                "result_source",
                "business_outcome",
                "result_code",
                "result_message",
                "retryable",
                "manual_action_url",
                "manual_code",
            }.issubset(columns)
        )
        self.assertEqual(task_count, 0)
        self.assertEqual(app_count, 0)

    def test_existing_app_table_receives_soft_archive_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            db_path = data_dir / "spiderfly.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
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
                    )
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
                database.init_db()

            with closing(sqlite3.connect(db_path)) as conn:
                columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(rpa_apps)")
                }

        self.assertTrue({"archived", "archived_at"}.issubset(columns))

    def test_execution_output_keeps_the_latest_tail_when_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            db_path = data_dir / "output.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "CREATE TABLE executions (id INTEGER PRIMARY KEY, stdout TEXT NOT NULL DEFAULT '', stderr TEXT NOT NULL DEFAULT '')"
                )
                conn.execute("INSERT INTO executions (id) VALUES (1)")
                conn.commit()
            with (
                patch.object(database, "DATA_DIR", data_dir),
                patch.object(database, "DB_PATH", db_path),
                patch.object(database, "MAX_OUTPUT_CHARS", 40),
            ):
                database.append_execution_output(1, "stdout", "old-prefix-1234567890")
                database.append_execution_output(1, "stdout", "-latest-tail-ABCDEFGHIJ")
            with closing(sqlite3.connect(db_path)) as conn:
                value = conn.execute(
                    "SELECT stdout FROM executions WHERE id = 1"
                ).fetchone()[0]

        self.assertLessEqual(len(value), 40)
        self.assertTrue(value.startswith("[前方日志已截断]"))
        self.assertTrue(value.endswith("latest-tail-ABCDEFGHIJ"))


if __name__ == "__main__":
    unittest.main()
