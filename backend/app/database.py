from __future__ import annotations

import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import DEFAULT_TASK_TIMEOUT_SECONDS, DATA_DIR, RPA_APPS_DIR, RPA_ENVS_DIR


DB_PATH = DATA_DIR / "spiderfly.db"
_DB_LOCK = threading.RLock()
MAX_OUTPUT_CHARS = 1_000_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Run a short, process-local database transaction under the shared lock."""
    with _DB_LOCK, connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _unique_app_name(conn: sqlite3.Connection, preferred: str) -> str:
    base = (preferred or "Python 应用").strip()[:100] or "Python 应用"
    candidate = base
    suffix = 2
    while conn.execute("SELECT 1 FROM rpa_apps WHERE name = ?", (candidate,)).fetchone():
        tail = f" ({suffix})"
        candidate = f"{base[: max(1, 100 - len(tail))]}{tail}"
        suffix += 1
    return candidate


def _migrate_legacy_apps(conn: sqlite3.Connection) -> None:
    RPA_APPS_DIR.mkdir(parents=True, exist_ok=True)
    RPA_ENVS_DIR.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        "SELECT id, name, app_name, script_path, app_id FROM tasks ORDER BY id"
    ).fetchall()
    for task in rows:
        if task["app_id"]:
            continue
        source = Path(task["script_path"]).expanduser()
        try:
            source_key = str(source.resolve()).lower()
        except OSError:
            source_key = str(source).lower()

        now = utc_now()
        app_name = _unique_app_name(
            conn, task["app_name"] or source.stem or task["name"]
        )
        cursor = conn.execute(
            """
            INSERT INTO rpa_apps (
                name, script_filename, script_path, requirements_text,
                env_path, environment_status, environment_error,
                install_log, revision, legacy_source_path,
                created_at, updated_at
            ) VALUES (?, 'main.py', '', '', '', 'pending', '', '', 1, ?, ?, ?)
            """,
            (app_name, source_key, now, now),
        )
        app_id = int(cursor.lastrowid)
        app_dir = RPA_APPS_DIR / str(app_id)
        app_dir.mkdir(parents=True, exist_ok=True)
        destination = app_dir / "main.py"
        status = "pending"
        error = ""
        try:
            if source.suffix.lower() != ".py" or not source.is_file():
                raise FileNotFoundError(f"原脚本不存在或不是 .py：{source}")
            shutil.copy2(source, destination)
            (app_dir / "requirements.txt").write_text("", encoding="utf-8")
        except Exception as exc:
            status = "failed"
            error = str(exc)[:1000]
        conn.execute(
            """
            UPDATE rpa_apps
            SET script_path = ?, environment_status = ?, environment_error = ?
            WHERE id = ?
            """,
            (str(destination.resolve()), status, error, app_id),
        )

        app = conn.execute("SELECT * FROM rpa_apps WHERE id = ?", (app_id,)).fetchone()
        conn.execute(
            """
            UPDATE tasks
            SET app_id = ?, app_name = ?, script_path = ?, python_path = '', version = 1
            WHERE id = ?
            """,
            (app_id, app["name"], app["script_path"], task["id"]),
        )


def _recover_interrupted_work(conn: sqlite3.Connection) -> None:
    now = utc_now()
    conn.execute(
        """
        UPDATE rpa_apps
        SET environment_status = 'pending',
            environment_error = '上次环境构建意外中断，已等待重新构建',
            updated_at = ?
        WHERE environment_status = 'building' AND archived = 0
        """,
        (now,),
    )
    running = conn.execute(
        "SELECT id, task_id FROM executions WHERE status = 'running'"
    ).fetchall()
    if running:
        conn.execute(
            """
            UPDATE executions
            SET status = 'failed', ended_at = ?,
                error_message = 'SpiderFly 上次运行意外中断，已在重启时结束记录'
            WHERE status = 'running'
            """,
            (now,),
        )
        task_ids = {int(row["task_id"]) for row in running}
        for task_id in task_ids:
            conn.execute(
                "UPDATE tasks SET last_status = 'failed', updated_at = ? WHERE id = ?",
                (now, task_id),
            )

    duplicate_rows = conn.execute(
        """
        SELECT task_id, MIN(id) AS keep_id
        FROM executions
        WHERE status = 'pending'
        GROUP BY task_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for row in duplicate_rows:
        conn.execute(
            """
            UPDATE executions
            SET status = 'cancelled', ended_at = ?,
                error_message = '重启恢复时合并了重复排队记录'
            WHERE task_id = ? AND status = 'pending' AND id != ?
            """,
            (now, row["task_id"], row["keep_id"]),
        )


def init_db() -> None:
    with _DB_LOCK, connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'operator',
                active INTEGER NOT NULL DEFAULT 1,
                must_change_password INTEGER NOT NULL DEFAULT 1,
                last_login_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rpa_apps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                script_filename TEXT NOT NULL DEFAULT 'main.py',
                script_path TEXT NOT NULL DEFAULT '',
                template_filename TEXT NOT NULL DEFAULT '',
                template_path TEXT NOT NULL DEFAULT '',
                requirements_text TEXT NOT NULL DEFAULT '',
                env_path TEXT NOT NULL DEFAULT '',
                environment_status TEXT NOT NULL DEFAULT 'pending',
                environment_error TEXT NOT NULL DEFAULT '',
                install_log TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 1,
                legacy_source_path TEXT NOT NULL DEFAULT '',
                archived INTEGER NOT NULL DEFAULT 0,
                archived_at TEXT,
                created_by INTEGER,
                updated_by INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY(updated_by) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                app_id INTEGER,
                app_name TEXT NOT NULL DEFAULT '',
                script_path TEXT NOT NULL,
                python_path TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                trigger_type TEXT NOT NULL DEFAULT 'manual',
                trigger_config TEXT NOT NULL DEFAULT '{}',
                next_run_at TEXT,
                last_triggered_at TEXT,
                timeout_seconds INTEGER NOT NULL DEFAULT 600,
                notify_on_success INTEGER NOT NULL DEFAULT 1,
                notify_on_failure INTEGER NOT NULL DEFAULT 1,
                last_status TEXT NOT NULL DEFAULT 'idle',
                last_run_at TEXT,
                archived INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER,
                updated_by INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(app_id) REFERENCES rpa_apps(id) ON DELETE RESTRICT,
                FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY(updated_by) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                trigger_source TEXT NOT NULL DEFAULT 'manual',
                requested_by INTEGER,
                script_path_snapshot TEXT NOT NULL DEFAULT '',
                python_path_snapshot TEXT NOT NULL DEFAULT '',
                started_at TEXT,
                ended_at TEXT,
                duration_ms INTEGER,
                exit_code INTEGER,
                stdout TEXT NOT NULL DEFAULT '',
                stderr TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                result_source TEXT NOT NULL DEFAULT 'legacy',
                business_outcome TEXT NOT NULL DEFAULT '',
                result_code TEXT NOT NULL DEFAULT '',
                result_message TEXT NOT NULL DEFAULT '',
                retryable INTEGER,
                manual_action_url TEXT NOT NULL DEFAULT '',
                manual_code TEXT NOT NULL DEFAULT '',
                notification_status TEXT NOT NULL DEFAULT 'pending',
                notification_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY(requested_by) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                target_type TEXT NOT NULL DEFAULT '',
                target_id INTEGER,
                summary TEXT NOT NULL DEFAULT '',
                ip_address TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_executions_task_id
                ON executions(task_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_executions_status
                ON executions(status, id ASC);
            CREATE INDEX IF NOT EXISTS idx_sessions_expiry
                ON sessions(expires_at);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_created
                ON audit_logs(id DESC);
            """
        )

        app_columns = _column_names(conn, "rpa_apps")
        app_migrations = {
            "archived": "ALTER TABLE rpa_apps ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
            "archived_at": "ALTER TABLE rpa_apps ADD COLUMN archived_at TEXT",
            "template_filename": "ALTER TABLE rpa_apps ADD COLUMN template_filename TEXT NOT NULL DEFAULT ''",
            "template_path": "ALTER TABLE rpa_apps ADD COLUMN template_path TEXT NOT NULL DEFAULT ''",
        }
        for column, statement in app_migrations.items():
            if column not in app_columns:
                conn.execute(statement)

        task_columns = _column_names(conn, "tasks")
        task_migrations = {
            "app_name": "ALTER TABLE tasks ADD COLUMN app_name TEXT NOT NULL DEFAULT ''",
            "app_id": "ALTER TABLE tasks ADD COLUMN app_id INTEGER",
            "trigger_type": "ALTER TABLE tasks ADD COLUMN trigger_type TEXT NOT NULL DEFAULT 'manual'",
            "trigger_config": "ALTER TABLE tasks ADD COLUMN trigger_config TEXT NOT NULL DEFAULT '{}'",
            "next_run_at": "ALTER TABLE tasks ADD COLUMN next_run_at TEXT",
            "last_triggered_at": "ALTER TABLE tasks ADD COLUMN last_triggered_at TEXT",
            "archived": "ALTER TABLE tasks ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
            "version": "ALTER TABLE tasks ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
            "created_by": "ALTER TABLE tasks ADD COLUMN created_by INTEGER",
            "updated_by": "ALTER TABLE tasks ADD COLUMN updated_by INTEGER",
        }
        for column, statement in task_migrations.items():
            if column not in task_columns:
                conn.execute(statement)
        conn.execute(
            """
            UPDATE tasks SET timeout_seconds = ?
            WHERE timeout_seconds IS NULL OR timeout_seconds != ?
            """,
            (DEFAULT_TASK_TIMEOUT_SECONDS, DEFAULT_TASK_TIMEOUT_SECONDS),
        )

        execution_columns = _column_names(conn, "executions")
        execution_migrations = {
            "trigger_source": "ALTER TABLE executions ADD COLUMN trigger_source TEXT NOT NULL DEFAULT 'manual'",
            "requested_by": "ALTER TABLE executions ADD COLUMN requested_by INTEGER",
            "script_path_snapshot": "ALTER TABLE executions ADD COLUMN script_path_snapshot TEXT NOT NULL DEFAULT ''",
            "python_path_snapshot": "ALTER TABLE executions ADD COLUMN python_path_snapshot TEXT NOT NULL DEFAULT ''",
            "result_source": "ALTER TABLE executions ADD COLUMN result_source TEXT NOT NULL DEFAULT 'legacy'",
            "business_outcome": "ALTER TABLE executions ADD COLUMN business_outcome TEXT NOT NULL DEFAULT ''",
            "result_code": "ALTER TABLE executions ADD COLUMN result_code TEXT NOT NULL DEFAULT ''",
            "result_message": "ALTER TABLE executions ADD COLUMN result_message TEXT NOT NULL DEFAULT ''",
            "retryable": "ALTER TABLE executions ADD COLUMN retryable INTEGER",
            "manual_action_url": "ALTER TABLE executions ADD COLUMN manual_action_url TEXT NOT NULL DEFAULT ''",
            "manual_code": "ALTER TABLE executions ADD COLUMN manual_code TEXT NOT NULL DEFAULT ''",
        }
        for column, statement in execution_migrations.items():
            if column not in execution_columns:
                conn.execute(statement)

        _migrate_legacy_apps(conn)
        _recover_interrupted_work(conn)
        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS trg_tasks_reject_archived_app_insert
            BEFORE INSERT ON tasks
            WHEN NEW.app_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM rpa_apps
                WHERE id = NEW.app_id AND archived = 1
            )
            BEGIN
                SELECT RAISE(ABORT, 'app_archived');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_tasks_reject_archived_app_update
            BEFORE UPDATE OF app_id ON tasks
            WHEN NEW.app_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM rpa_apps
                WHERE id = NEW.app_id AND archived = 1
            )
            BEGIN
                SELECT RAISE(ABORT, 'app_archived');
            END;
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_executions_one_active_task
            ON executions(task_id)
            WHERE status IN ('pending', 'running')
            """
        )
        shared_app = conn.execute(
            """
            SELECT app_id, COUNT(*) AS task_count
            FROM tasks
            WHERE archived = 0 AND app_id IS NOT NULL
            GROUP BY app_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone()
        if not shared_app:
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_one_active_app
                ON tasks(app_id)
                WHERE archived = 0 AND app_id IS NOT NULL
                """
            )


def create_backup_if_missing(destination: Path) -> Path:
    """Create one crash-safe SQLite backup beside the live database.

    The migration caller deliberately reuses the first backup on retries so a
    failed filesystem cleanup cannot overwrite the original recovery point.
    """
    source = DB_PATH.expanduser().absolute()
    target = destination.expanduser().absolute()
    if target == source or target.parent != source.parent:
        raise ValueError("数据库备份必须位于 SpiderFly 数据库所在目录")
    temporary = target.with_name(f".{target.name}.tmp")
    with _DB_LOCK:
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        if temporary.exists():
            temporary.unlink()
        source_conn: sqlite3.Connection | None = None
        target_conn: sqlite3.Connection | None = None
        try:
            source_conn = sqlite3.connect(source, timeout=30)
            target_conn = sqlite3.connect(temporary, timeout=30)
            source_conn.backup(target_conn)
            target_conn.commit()
        except Exception:
            if temporary.exists():
                temporary.unlink()
            raise
        finally:
            if target_conn is not None:
                target_conn.close()
            if source_conn is not None:
                source_conn.close()
        temporary.replace(target)
    return target


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


def execute_result(query: str, params: tuple[Any, ...] = ()) -> tuple[int, int]:
    with _DB_LOCK, connection() as conn:
        cursor = conn.execute(query, params)
        return int(cursor.lastrowid or 0), int(cursor.rowcount)


def append_execution_output(execution_id: int, field: str, text: str) -> None:
    if field not in {"stdout", "stderr"}:
        raise ValueError("Unsupported output field")
    truncation_marker = "[前方日志已截断]\n"
    tail_chars = max(1, MAX_OUTPUT_CHARS - len(truncation_marker))
    with _DB_LOCK, connection() as conn:
        conn.execute(
            f"""
            UPDATE executions
            SET {field} = CASE
                WHEN length({field}) + length(?) <= ? THEN {field} || ?
                ELSE ? || substr({field} || ?, -?)
            END
            WHERE id = ?
            """,
            (
                text,
                MAX_OUTPUT_CHARS,
                text,
                truncation_marker,
                text,
                tail_chars,
                execution_id,
            ),
        )
