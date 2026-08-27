from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("SPIDERFLY_DATA_DIR", PROJECT_ROOT / "data")).resolve()
DB_PATH = DATA_DIR / "spiderfly.db"
_DB_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with _DB_LOCK, connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
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

            CREATE TABLE IF NOT EXISTS executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                started_at TEXT,
                ended_at TEXT,
                duration_ms INTEGER,
                exit_code INTEGER,
                stdout TEXT NOT NULL DEFAULT '',
                stderr TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                notification_status TEXT NOT NULL DEFAULT 'pending',
                notification_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_executions_task_id
                ON executions(task_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_executions_status
                ON executions(status, id DESC);
            """
        )

        task_columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        task_migrations = {
            "app_name": "ALTER TABLE tasks ADD COLUMN app_name TEXT NOT NULL DEFAULT ''",
            "trigger_type": "ALTER TABLE tasks ADD COLUMN trigger_type TEXT NOT NULL DEFAULT 'manual'",
            "trigger_config": "ALTER TABLE tasks ADD COLUMN trigger_config TEXT NOT NULL DEFAULT '{}'",
            "next_run_at": "ALTER TABLE tasks ADD COLUMN next_run_at TEXT",
            "last_triggered_at": "ALTER TABLE tasks ADD COLUMN last_triggered_at TEXT",
        }
        for column, statement in task_migrations.items():
            if column not in task_columns:
                conn.execute(statement)

        execution_columns = {row[1] for row in conn.execute("PRAGMA table_info(executions)").fetchall()}
        if "trigger_source" not in execution_columns:
            conn.execute(
                "ALTER TABLE executions ADD COLUMN trigger_source TEXT NOT NULL DEFAULT 'manual'"
            )

        for row in conn.execute("SELECT id, script_path FROM tasks WHERE app_name = ''").fetchall():
            conn.execute(
                "UPDATE tasks SET app_name = ? WHERE id = ?",
                (Path(row["script_path"]).stem, row["id"]),
            )

        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        if count == 0:
            sample_path = (PROJECT_ROOT / "sample_scripts" / "1.py").resolve()
            now = utc_now()
            conn.execute(
                """
                INSERT INTO tasks (
                    name, description, script_path, enabled, timeout_seconds,
                    notify_on_success, notify_on_failure, created_at, updated_at
                ) VALUES (?, ?, ?, 1, 0, 1, 1, ?, ?)
                """,
                (
                    "你好 flyyang",
                    "SpiderFly 本地直跑与日志监控示例",
                    str(sample_path),
                    now,
                    now,
                ),
            )


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _DB_LOCK, connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with _DB_LOCK, connection() as conn:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else None


def execute(query: str, params: tuple[Any, ...] = ()) -> int:
    with _DB_LOCK, connection() as conn:
        cursor = conn.execute(query, params)
        return int(cursor.lastrowid or 0)


def append_execution_output(execution_id: int, field: str, text: str) -> None:
    if field not in {"stdout", "stderr"}:
        raise ValueError("Unsupported output field")
    with _DB_LOCK, connection() as conn:
        conn.execute(
            f"UPDATE executions SET {field} = {field} || ? WHERE id = ?",
            (text, execution_id),
        )
