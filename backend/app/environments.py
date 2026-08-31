from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import uuid
from pathlib import Path

from . import database
from .config import (
    BASE_PYTHON,
    DEFAULT_TASK_TIMEOUT_SECONDS,
    ENV_VERIFY_TIMEOUT_SECONDS,
    PIP_TIMEOUT_SECONDS,
    RPA_APPS_DIR,
    RPA_ENVS_DIR,
    VENV_TIMEOUT_SECONDS,
)
from .database import execute, execute_result, fetch_all, fetch_one, transaction, utc_now
from .execution_results import remove_execution_workspaces


MAX_SCRIPT_BYTES = 2 * 1024 * 1024
MAX_TEMPLATE_BYTES = 50 * 1024 * 1024
MAX_REQUIREMENTS_CHARS = 20_000
MAX_INSTALL_LOG_CHARS = 60_000
PROCESS_TERMINATION_SECONDS = 8
PIPE_DRAIN_SECONDS = 5
_ENV_DIR_PATTERN = re.compile(r"^app_(\d+)_r(\d+)_[0-9a-f]{8}$")
_APP_STORAGE_LOCK = threading.RLock()
logger = logging.getLogger(__name__)
_ONE_TASK_ONE_APP_MIGRATION = "one-task-one-app-v1"


def _safe_requirements(value: str) -> str:
    value = value.replace("\r\n", "\n").strip()
    if len(value) > MAX_REQUIREMENTS_CHARS:
        raise ValueError("依赖清单过长")
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lowered = line.lower()
        if (
            line.startswith("-")
            or "://" in lowered
            or lowered.startswith("git+")
            or " @ " in line
            or line.startswith(("/", "\\", ".\\", ".."))
        ):
            raise ValueError("依赖清单只允许填写 PyPI 包名和版本，不允许 URL、参数或本地路径")
    return value


def validate_python_upload(filename: str, content: bytes) -> tuple[str, str]:
    safe_name = Path(filename or "main.py").name
    if safe_name != filename or Path(safe_name).suffix.lower() != ".py":
        raise ValueError("只能上传单个 .py 文件")
    if not content or len(content) > MAX_SCRIPT_BYTES:
        raise ValueError("Python 文件不能为空且不能超过 2MB")
    try:
        source = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Python 文件必须使用 UTF-8 编码") from exc
    try:
        compile(source, safe_name, "exec")
    except SyntaxError as exc:
        raise ValueError(f"Python 语法检查失败：第 {exc.lineno or '?'} 行 {exc.msg}") from exc
    return safe_name, source


def validate_template_upload(filename: str, content: bytes) -> str:
    safe_name = Path(filename or "").name
    allowed = {".xlsx", ".xlsm", ".xltx", ".xltm"}
    if not safe_name or safe_name != filename or Path(safe_name).suffix.lower() not in allowed:
        raise ValueError("Excel 模板只支持 .xlsx、.xlsm、.xltx 或 .xltm 文件")
    if not content or len(content) > MAX_TEMPLATE_BYTES:
        raise ValueError("Excel 模板不能为空且不能超过 50MB")
    return safe_name


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", lambda: False)
    try:
        return path.is_symlink() or bool(is_junction())
    except OSError:
        return True


def _validated_managed_directory(
    candidate: Path,
    *,
    root: Path,
    expected_name: str | None = None,
    app_id: int | None = None,
) -> Path | None:
    """Resolve one direct, real directory without following a reparse point."""
    try:
        lexical_root = root.expanduser().absolute()
        lexical_candidate = candidate.expanduser().absolute()
        if lexical_candidate.parent != lexical_root:
            return None
        if expected_name is not None and lexical_candidate.name != expected_name:
            return None
        if _is_link_or_junction(lexical_candidate):
            return None
        resolved_root = lexical_root.resolve()
        resolved_candidate = lexical_candidate.resolve()
        if resolved_candidate.parent != resolved_root:
            return None
        if app_id is not None:
            match = _ENV_DIR_PATTERN.fullmatch(resolved_candidate.name)
            if not match or int(match.group(1)) != int(app_id):
                return None
        if not resolved_candidate.is_dir():
            return None
        return resolved_candidate
    except OSError:
        return None


def _matching_environment_directories(app_id: int) -> list[Path]:
    root = RPA_ENVS_DIR.expanduser().absolute()
    if not root.is_dir():
        return []
    matches: list[Path] = []
    for candidate in root.iterdir():
        match = _ENV_DIR_PATTERN.fullmatch(candidate.name)
        if match and int(match.group(1)) == int(app_id):
            matches.append(candidate)
    return matches


def remove_managed_app_storage(app_id: int) -> tuple[str, ...]:
    """Delete only this application's exact managed source and venv directories.

    Missing directories are treated as already clean. A matching symlink, junction,
    file, or deletion error is reported instead of being followed or ignored.
    """
    if int(app_id) < 1:
        raise ValueError("应用编号无效")
    removed: list[str] = []
    failures: list[str] = []

    app_candidate = RPA_APPS_DIR.expanduser().absolute() / str(int(app_id))
    if os.path.lexists(app_candidate):
        app_dir = _validated_managed_directory(
            app_candidate,
            root=RPA_APPS_DIR,
            expected_name=str(int(app_id)),
        )
        if app_dir is None:
            failures.append(str(app_candidate))
        else:
            try:
                shutil.rmtree(app_dir)
                removed.append(str(app_dir))
            except OSError:
                failures.append(str(app_dir))

    try:
        environment_candidates = _matching_environment_directories(app_id)
    except OSError:
        environment_candidates = []
        failures.append(str(RPA_ENVS_DIR))
    for candidate in environment_candidates:
        env_dir = _validated_managed_directory(
            candidate, root=RPA_ENVS_DIR, app_id=app_id
        )
        if env_dir is None:
            failures.append(str(candidate))
            continue
        try:
            shutil.rmtree(env_dir)
            removed.append(str(env_dir))
        except OSError:
            failures.append(str(env_dir))

    if failures:
        names = "、".join(Path(item).name or item for item in failures[:5])
        raise RuntimeError(f"这些程序目录未能安全删除：{names}")
    return tuple(removed)


def _legacy_cleanup_paths() -> tuple[Path, Path]:
    data_dir = database.DB_PATH.expanduser().absolute().parent
    return (
        data_dir / f".{_ONE_TASK_ONE_APP_MIGRATION}.done.json",
        data_dir / f"spiderfly.before-{_ONE_TASK_ONE_APP_MIGRATION}.db",
    )


def _legacy_cleanup_plan(conn: sqlite3.Connection) -> dict[str, tuple]:
    running = conn.execute(
        "SELECT id FROM executions WHERE status = 'running' ORDER BY id LIMIT 1"
    ).fetchone()
    if running:
        raise RuntimeError(
            f"检测到仍在运行的执行记录 #{int(running['id'])}，已停止旧数据清理"
        )

    invalid_task = conn.execute(
        """
        SELECT t.id, t.app_id,
               CASE
                   WHEN t.app_id IS NULL THEN 'missing'
                   WHEN a.id IS NULL THEN 'missing'
                   WHEN a.archived = 1 THEN 'archived'
                   ELSE ''
               END AS reason
        FROM tasks t
        LEFT JOIN rpa_apps a ON a.id = t.app_id
        WHERE t.archived = 0
          AND (t.app_id IS NULL OR a.id IS NULL OR a.archived = 1)
        ORDER BY t.id
        LIMIT 1
        """
    ).fetchone()
    if invalid_task:
        reason = (
            "对应程序已归档"
            if invalid_task["reason"] == "archived"
            else "缺少对应程序"
        )
        raise RuntimeError(
            f"有效任务 #{int(invalid_task['id'])}{reason}，已停止旧数据清理"
        )

    duplicate = conn.execute(
        """
        SELECT app_id, COUNT(*) AS task_count
        FROM tasks
        WHERE archived = 0 AND app_id IS NOT NULL
        GROUP BY app_id
        HAVING COUNT(*) > 1
        ORDER BY app_id
        LIMIT 1
        """
    ).fetchone()
    if duplicate:
        raise RuntimeError(
            f"程序 #{int(duplicate['app_id'])} 仍绑定 {int(duplicate['task_count'])} 个有效任务，"
            "已停止旧数据清理"
        )

    active_pairs = tuple(
        (int(row["id"]), int(row["app_id"]))
        for row in conn.execute(
            "SELECT id, app_id FROM tasks WHERE archived = 0 ORDER BY id"
        ).fetchall()
    )
    archived_task_ids = tuple(
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM tasks WHERE archived = 1 ORDER BY id"
        ).fetchall()
    )
    archived_execution_ids = tuple(
        int(row["id"])
        for row in conn.execute(
            """
            SELECT e.id
            FROM executions e
            JOIN tasks t ON t.id = e.task_id
            WHERE t.archived = 1
            ORDER BY e.id
            """
        ).fetchall()
    )
    orphan_app_ids = tuple(
        int(row["id"])
        for row in conn.execute(
            """
            SELECT a.id
            FROM rpa_apps a
            WHERE NOT EXISTS (
                SELECT 1 FROM tasks t
                WHERE t.app_id = a.id AND t.archived = 0
            )
            ORDER BY a.id
            """
        ).fetchall()
    )
    return {
        "active_pairs": active_pairs,
        "archived_task_ids": archived_task_ids,
        "archived_execution_ids": archived_execution_ids,
        "orphan_app_ids": orphan_app_ids,
    }


def _write_legacy_cleanup_marker(marker_path: Path, payload: dict) -> None:
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker_path.with_name(f".{marker_path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(marker_path)


def cleanup_legacy_task_program_model() -> dict:
    """One-time migration from the old program/task layers to strict one-to-one data.

    Every active task and its application/history are protected. Archived tasks and
    their execution workspaces are removed. Applications without an active task are
    then hard-deleted with their managed source and virtual environments.

    All structural checks happen before the first mutation. Files are cleaned before
    database rows, so a filesystem failure leaves SQLite untouched and a retry is
    safe. The first database backup is retained until an operator removes it.
    """
    marker_path, backup_path = _legacy_cleanup_paths()
    if marker_path.is_file():
        return {
            "status": "already_complete",
            "deleted_task_count": 0,
            "deleted_app_count": 0,
            "removed_directory_count": 0,
            "marker_path": str(marker_path),
            "backup_path": str(backup_path) if backup_path.is_file() else "",
        }

    with _APP_STORAGE_LOCK:
        with transaction() as conn:
            plan = _legacy_cleanup_plan(conn)

        has_changes = bool(plan["archived_task_ids"] or plan["orphan_app_ids"])
        if has_changes:
            database.create_backup_if_missing(backup_path)

        removed_execution_dirs: tuple[str, ...] = ()
        removed_app_dirs: list[str] = []
        if plan["archived_execution_ids"]:
            removed_execution_dirs = remove_execution_workspaces(
                plan["archived_execution_ids"]
            )
        for app_id in plan["orphan_app_ids"]:
            removed_app_dirs.extend(remove_managed_app_storage(int(app_id)))

        deleted_task_count = 0
        deleted_app_count = 0
        if has_changes:
            with transaction() as conn:
                current_plan = _legacy_cleanup_plan(conn)
                if current_plan != plan:
                    raise RuntimeError(
                        "清理期间任务或程序数据发生变化，数据库未修改，请重启后重试"
                    )

                if plan["archived_task_ids"]:
                    placeholders = ",".join("?" for _ in plan["archived_task_ids"])
                    cursor = conn.execute(
                        f"DELETE FROM tasks WHERE archived = 1 AND id IN ({placeholders})",
                        plan["archived_task_ids"],
                    )
                    deleted_task_count = int(cursor.rowcount)
                    if deleted_task_count != len(plan["archived_task_ids"]):
                        raise RuntimeError("归档任务数量发生变化，数据库未修改")

                if plan["orphan_app_ids"]:
                    placeholders = ",".join("?" for _ in plan["orphan_app_ids"])
                    cursor = conn.execute(
                        f"""
                        DELETE FROM rpa_apps
                        WHERE id IN ({placeholders})
                          AND NOT EXISTS (
                              SELECT 1 FROM tasks t
                              WHERE t.app_id = rpa_apps.id AND t.archived = 0
                          )
                        """,
                        plan["orphan_app_ids"],
                    )
                    deleted_app_count = int(cursor.rowcount)
                    if deleted_app_count != len(plan["orphan_app_ids"]):
                        raise RuntimeError("遗留程序数量发生变化，数据库未修改")

        completed_at = utc_now()
        result = {
            "status": "complete",
            "completed_at": completed_at,
            "deleted_task_count": deleted_task_count,
            "deleted_app_count": deleted_app_count,
            "removed_directory_count": len(removed_execution_dirs)
            + len(removed_app_dirs),
            "marker_path": str(marker_path),
            "backup_path": str(backup_path) if has_changes else "",
        }
        _write_legacy_cleanup_marker(marker_path, result)
        logger.info(
            "旧程序/任务模型清理完成：归档任务 %s 个，遗留程序 %s 个，目录 %s 个",
            deleted_task_count,
            deleted_app_count,
            result["removed_directory_count"],
        )
        return result


def delete_managed_app(app_id: int) -> dict:
    """Hard-delete one unbound app and every managed source/venv directory."""
    if int(app_id) < 1:
        raise ValueError("程序编号无效")
    with _APP_STORAGE_LOCK:
        with transaction() as conn:
            app = conn.execute(
                "SELECT * FROM rpa_apps WHERE id = ? AND archived = 0", (app_id,)
            ).fetchone()
            if not app:
                raise FileNotFoundError("自动化程序不存在或已经删除")
            bound = conn.execute(
                "SELECT id FROM tasks WHERE app_id = ? LIMIT 1", (app_id,)
            ).fetchone()
            if bound:
                raise RuntimeError("这个程序已经绑定任务，请从任务列表删除整项")
            if app["environment_status"] in {"pending", "building"}:
                raise RuntimeError("程序正在准备运行环境，请完成后再删除")
            previous_status = str(app["environment_status"])
            app_name = str(app["name"])
            conn.execute(
                "UPDATE rpa_apps SET environment_status = 'removing', environment_error = '' WHERE id = ?",
                (app_id,),
            )
        try:
            removed = remove_managed_app_storage(app_id)
        except (OSError, RuntimeError, ValueError) as exc:
            execute(
                "UPDATE rpa_apps SET environment_status = 'failed', environment_error = ?, updated_at = ? WHERE id = ?",
                (f"删除未完成：{exc}", utc_now(), app_id),
            )
            raise RuntimeError(str(exc)) from exc
        with transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM rpa_apps WHERE id = ? AND archived = 0 AND environment_status = 'removing'",
                (app_id,),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("程序状态已经变化，请刷新后重试")
        return {
            "id": int(app_id),
            "name": app_name,
            "removed_directory_count": len(removed),
            "previous_environment_status": previous_status,
        }


def delete_task_bundle(task_id: int) -> dict:
    """Hard-delete a task, its history, its one-to-one app and all managed files."""
    if int(task_id) < 1:
        raise ValueError("任务编号无效")
    with _APP_STORAGE_LOCK:
        with transaction() as conn:
            task = conn.execute(
                "SELECT * FROM tasks WHERE id = ? AND archived = 0", (task_id,)
            ).fetchone()
            if not task:
                raise FileNotFoundError("任务不存在或已经删除")
            running = conn.execute(
                "SELECT id FROM executions WHERE task_id = ? AND status = 'running' LIMIT 1",
                (task_id,),
            ).fetchone()
            if running:
                raise RuntimeError("任务正在运行，请运行结束后再删除")
            execution_ids = tuple(
                int(item["id"])
                for item in conn.execute(
                    "SELECT id FROM executions WHERE task_id = ? ORDER BY id", (task_id,)
                ).fetchall()
            )
            app_id = int(task["app_id"] or 0)
            app_name = str(task["app_name"] or "")
            if app_id:
                app = conn.execute(
                    "SELECT * FROM rpa_apps WHERE id = ? AND archived = 0", (app_id,)
                ).fetchone()
                if not app:
                    raise RuntimeError("任务对应的程序不存在，请管理员检查")
                other_task = conn.execute(
                    "SELECT name FROM tasks WHERE app_id = ? AND id != ? LIMIT 1",
                    (app_id, task_id),
                ).fetchone()
                if other_task:
                    raise RuntimeError("这个旧程序仍被其他任务使用，暂时不能整项删除")
                if app["environment_status"] in {"pending", "building"}:
                    raise RuntimeError("程序正在准备运行环境，请完成后再删除")
                app_name = str(app["name"])
                conn.execute(
                    "UPDATE rpa_apps SET environment_status = 'removing', environment_error = '' WHERE id = ?",
                    (app_id,),
                )
            conn.execute(
                "DELETE FROM executions WHERE task_id = ? AND status = 'pending'", (task_id,)
            )
        removed_execution_dirs: tuple[str, ...] = ()
        removed_app_dirs: tuple[str, ...] = ()
        try:
            removed_execution_dirs = remove_execution_workspaces(execution_ids)
            if app_id:
                removed_app_dirs = remove_managed_app_storage(app_id)
        except (OSError, RuntimeError, ValueError) as exc:
            if app_id:
                execute(
                    "UPDATE rpa_apps SET environment_status = 'failed', environment_error = ?, updated_at = ? WHERE id = ?",
                    (f"删除未完成：{exc}", utc_now(), app_id),
                )
            raise RuntimeError(str(exc)) from exc
        with transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM tasks WHERE id = ? AND archived = 0", (task_id,)
            )
            if cursor.rowcount != 1:
                raise RuntimeError("任务状态已经变化，请刷新后重试")
            if app_id:
                app_cursor = conn.execute(
                    "DELETE FROM rpa_apps WHERE id = ? AND archived = 0 AND environment_status = 'removing'",
                    (app_id,),
                )
                if app_cursor.rowcount != 1:
                    raise RuntimeError("程序状态已经变化，请刷新后重试")
        return {
            "id": int(task_id),
            "name": str(task["name"]),
            "app_id": app_id,
            "app_name": app_name,
            "deleted_execution_count": len(execution_ids),
            "removed_directory_count": len(removed_execution_dirs) + len(removed_app_dirs),
        }


def _environment_from_python_snapshot(value: str, app_id: int) -> Path | None:
    if not value:
        return None
    try:
        python_path = Path(value).expanduser().resolve()
        env_dir = python_path.parent.parent
        validated = _validated_managed_directory(
            env_dir, root=RPA_ENVS_DIR, app_id=app_id
        )
        if validated is None or _environment_python(validated).resolve() != python_path:
            return None
        return validated
    except (OSError, IndexError):
        return None


def cleanup_old_environments(
    app_id: int,
    current_env_path: str,
    protected_python_paths: list[str] | tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Remove obsolete venvs while preserving the published and active-run venvs."""
    current = _validated_managed_directory(
        Path(current_env_path or ""), root=RPA_ENVS_DIR, app_id=app_id
    )
    if current is None:
        raise RuntimeError("当前 Python 环境路径不在 SpiderFly 受管目录内，已停止清理")
    protected = {current}
    for value in protected_python_paths:
        referenced = _environment_from_python_snapshot(value, app_id)
        if referenced is not None:
            protected.add(referenced)

    removed: list[str] = []
    failures: list[str] = []
    for candidate in _matching_environment_directories(app_id):
        env_dir = _validated_managed_directory(
            candidate, root=RPA_ENVS_DIR, app_id=app_id
        )
        if env_dir is None:
            failures.append(str(candidate))
            continue
        if env_dir in protected:
            continue
        try:
            shutil.rmtree(env_dir)
            removed.append(str(env_dir))
        except OSError:
            failures.append(str(env_dir))
    if failures:
        names = "、".join(Path(item).name or item for item in failures[:5])
        raise RuntimeError(f"旧 Python 环境未完全清理：{names}")
    return tuple(removed)


def create_managed_app(
    name: str,
    filename: str,
    content: bytes,
    requirements_text: str,
    user_id: int,
    template_filename: str = "",
    template_content: bytes | None = None,
) -> dict:
    with _APP_STORAGE_LOCK:
        return _create_managed_app_locked(
            name,
            filename,
            content,
            requirements_text,
            user_id,
            template_filename,
            template_content,
        )


def create_managed_task_bundle(
    name: str,
    filename: str,
    content: bytes,
    requirements_text: str,
    user_id: int,
    template_filename: str = "",
    template_content: bytes | None = None,
    *,
    description: str = "",
    trigger_type: str = "manual",
    trigger_config: str = "{}",
    next_run_at: str | None = None,
    enabled: bool = True,
    notify_on_success: bool = True,
    notify_on_failure: bool = True,
) -> dict:
    """Atomically create one managed program and its one-to-one configured task."""
    with _APP_STORAGE_LOCK:
        return _create_managed_app_locked(
            name,
            filename,
            content,
            requirements_text,
            user_id,
            template_filename,
            template_content,
            create_manual_task=True,
            task_description=description,
            task_trigger_type=trigger_type,
            task_trigger_config=trigger_config,
            task_next_run_at=next_run_at,
            task_enabled=enabled,
            task_notify_on_success=notify_on_success,
            task_notify_on_failure=notify_on_failure,
        )


def _create_managed_app_locked(
    name: str,
    filename: str,
    content: bytes,
    requirements_text: str,
    user_id: int,
    template_filename: str = "",
    template_content: bytes | None = None,
    *,
    create_manual_task: bool = False,
    task_description: str = "",
    task_trigger_type: str = "manual",
    task_trigger_config: str = "{}",
    task_next_run_at: str | None = None,
    task_enabled: bool = True,
    task_notify_on_success: bool = True,
    task_notify_on_failure: bool = True,
) -> dict:
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("应用名称不能为空且不能超过 100 个字符")
    safe_name, source = validate_python_upload(filename, content)
    if template_filename or template_content is not None:
        safe_template_name = validate_template_upload(
            template_filename, template_content or b""
        )
    else:
        safe_template_name = ""
    requirements = _safe_requirements(requirements_text)
    now = utc_now()
    app_id: int | None = None
    source_write_started = False
    try:
        with transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM rpa_apps WHERE name = ?", (name,)
            ).fetchone()
            if existing and not bool(existing["archived"]):
                raise sqlite3.IntegrityError("应用名称已存在")
            if existing:
                app_id = int(existing["id"])
                active_run = conn.execute(
                    """
                    SELECT e.id
                    FROM executions e
                    JOIN tasks t ON t.id = e.task_id
                    WHERE t.app_id = ? AND e.status IN ('pending', 'running')
                    LIMIT 1
                    """,
                    (app_id,),
                ).fetchone()
                if active_run:
                    raise RuntimeError("这个已移除程序仍有排队或运行记录，暂时不能重新上传")
                remove_managed_app_storage(app_id)
                revision = int(existing["revision"] or 1) + 1
                conn.execute(
                    """
                    UPDATE rpa_apps
                    SET script_filename = ?, script_path = '',
                        template_filename = ?, template_path = '', requirements_text = ?,
                        env_path = '', environment_status = 'pending',
                        environment_error = '', install_log = '', revision = ?,
                        legacy_source_path = '', archived = 0, archived_at = NULL,
                        updated_by = ?, updated_at = ?
                    WHERE id = ? AND archived = 1
                    """,
                    (
                        safe_name,
                        safe_template_name,
                        requirements,
                        revision,
                        user_id,
                        now,
                        app_id,
                    ),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO rpa_apps (
                        name, script_filename, script_path,
                        template_filename, template_path, requirements_text,
                        env_path, environment_status, environment_error,
                        install_log, revision, legacy_source_path, archived,
                        created_by, updated_by, created_at, updated_at
                    ) VALUES (?, ?, '', ?, '', ?, '', 'pending', '', '', 1, '', 0, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        safe_name,
                        safe_template_name,
                        requirements,
                        user_id,
                        user_id,
                        now,
                        now,
                    ),
                )
                app_id = int(cursor.lastrowid)

            app_dir = (RPA_APPS_DIR / str(app_id)).resolve()
            expected_parent = RPA_APPS_DIR.resolve()
            if app_dir.parent != expected_parent or app_dir.name != str(app_id):
                raise RuntimeError("应用源码目录不在 SpiderFly 管理范围内")
            source_write_started = True
            app_dir.mkdir(parents=True, exist_ok=True)
            script_path = app_dir / safe_name
            script_path.write_text(source, encoding="utf-8", newline="\n")
            template_path = ""
            if safe_template_name:
                managed_template = app_dir / safe_template_name
                managed_template.write_bytes(template_content or b"")
                template_path = str(managed_template.resolve())
            (app_dir / "requirements.txt").write_text(
                requirements + ("\n" if requirements else ""),
                encoding="utf-8",
                newline="\n",
            )
            conn.execute(
                """
                UPDATE rpa_apps
                SET script_path = ?, template_path = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(script_path.resolve()), template_path, utc_now(), app_id),
            )
            task_id: int | None = None
            if create_manual_task:
                task_cursor = conn.execute(
                    """
                    INSERT INTO tasks (
                        name, description, app_id, app_name, script_path, python_path,
                        enabled, trigger_type, trigger_config, next_run_at,
                        timeout_seconds, notify_on_success, notify_on_failure,
                        created_by, updated_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        task_description,
                        app_id,
                        name,
                        str(script_path.resolve()),
                        int(task_enabled),
                        task_trigger_type,
                        task_trigger_config,
                        task_next_run_at,
                        DEFAULT_TASK_TIMEOUT_SECONDS,
                        int(task_notify_on_success),
                        int(task_notify_on_failure),
                        user_id,
                        user_id,
                        now,
                        now,
                    ),
                )
                task_id = int(task_cursor.lastrowid)
            app = conn.execute(
                "SELECT * FROM rpa_apps WHERE id = ?", (app_id,)
            ).fetchone()
            assert app is not None
            result = dict(app)
            if task_id is not None:
                result["task_id"] = task_id
                result["active_task_count"] = 1
            return result
    except Exception:
        if app_id is not None and source_write_started:
            app_candidate = RPA_APPS_DIR.expanduser().absolute() / str(app_id)
            app_dir = _validated_managed_directory(
                app_candidate,
                root=RPA_APPS_DIR,
                expected_name=str(app_id),
            )
            if app_dir is not None:
                try:
                    shutil.rmtree(app_dir)
                except OSError:
                    logger.exception("清理应用 %s 的未提交源码目录失败", app_id)
        raise


def archive_managed_app(app_id: int, user_id: int) -> dict:
    """Stop offering an unused app and remove only its managed files.

    Task and execution rows remain untouched so audit/history pages can still
    explain what ran in the past. Storage cleanup happens only after the app is
    atomically hidden from task creation.
    """
    if int(app_id) < 1:
        raise ValueError("程序编号无效")

    with _APP_STORAGE_LOCK:
        with transaction() as conn:
            app = conn.execute(
                "SELECT * FROM rpa_apps WHERE id = ? AND archived = 0",
                (app_id,),
            ).fetchone()
            if not app:
                raise FileNotFoundError("自动化程序不存在或已经移除")
            if app["environment_status"] in {"pending", "building"}:
                raise RuntimeError("程序正在准备运行环境，请完成后再移除")

            active_tasks = conn.execute(
                """
                SELECT name FROM tasks
                WHERE app_id = ? AND archived = 0
                ORDER BY id
                """,
                (app_id,),
            ).fetchall()
            if active_tasks:
                names = "、".join(str(item["name"]) for item in active_tasks[:3])
                suffix = "等" if len(active_tasks) > 3 else ""
                raise RuntimeError(
                    f"还有 {len(active_tasks)} 个有效任务正在使用：{names}{suffix}。"
                    "请先归档这些任务"
                )

            active_execution = conn.execute(
                """
                SELECT e.id
                FROM executions e
                JOIN tasks t ON t.id = e.task_id
                WHERE t.app_id = ? AND e.status IN ('pending', 'running')
                LIMIT 1
                """,
                (app_id,),
            ).fetchone()
            if active_execution:
                raise RuntimeError("这个程序还有排队或运行记录，请结束后再移除")

            now = utc_now()
            cursor = conn.execute(
                """
                UPDATE rpa_apps
                SET archived = 1, archived_at = ?, updated_by = ?, updated_at = ?,
                    environment_status = 'removing', environment_error = ''
                WHERE id = ? AND archived = 0
                """,
                (now, user_id, now, app_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("程序状态已经变化，请刷新后重试")
            app_name = str(app["name"])

        removed: tuple[str, ...] = ()
        cleanup_warning = ""
        try:
            removed = remove_managed_app_storage(app_id)
        except RuntimeError as exc:
            cleanup_warning = str(exc)
            logger.warning("移除程序 %s 时未完全清理受管目录：%s", app_id, exc)

        execute(
            """
            UPDATE rpa_apps
            SET script_path = '', env_path = '', environment_status = 'removed',
                environment_error = ?, install_log = '', updated_at = ?
            WHERE id = ? AND archived = 1
            """,
            (cleanup_warning, utc_now(), app_id),
        )
        return {
            "id": int(app_id),
            "name": app_name,
            "removed_directory_count": len(removed),
            "cleanup_warning": cleanup_warning,
        }


def request_rebuild(app_id: int, user_id: int) -> dict:
    app = fetch_one(
        "SELECT * FROM rpa_apps WHERE id = ? AND archived = 0", (app_id,)
    )
    if not app:
        raise FileNotFoundError("自动化程序不存在或已经移除")
    _, rowcount = execute_result(
        """
        UPDATE rpa_apps
        SET environment_status = 'pending', environment_error = '', install_log = '',
            revision = revision + 1, updated_by = ?, updated_at = ?
        WHERE id = ? AND archived = 0
          AND environment_status IN ('ready', 'failed')
        """,
        (user_id, utc_now(), app_id),
    )
    if rowcount != 1:
        raise RuntimeError("环境已经在等待或正在构建，请稍后再试")
    updated = fetch_one("SELECT * FROM rpa_apps WHERE id = ?", (app_id,))
    assert updated is not None
    return updated


def pending_environment_ids() -> list[int]:
    return [
        int(item["id"])
        for item in fetch_all(
            """
            SELECT id FROM rpa_apps
            WHERE archived = 0 AND environment_status = 'pending'
            ORDER BY id
            """
        )
    ]


def _environment_python(env_dir: Path) -> Path:
    return env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _managed_runtime_paths(script_path: str, env_path: str) -> tuple[Path, Path]:
    script = Path(script_path or "").expanduser().resolve()
    env_dir = Path(env_path or "").expanduser().resolve()
    try:
        script.relative_to(RPA_APPS_DIR.resolve())
        env_dir.relative_to(RPA_ENVS_DIR.resolve())
    except ValueError as exc:
        raise RuntimeError("应用路径不在 SpiderFly 管理目录内") from exc
    python_path = _environment_python(env_dir).resolve()
    if script.suffix.lower() != ".py" or not script.is_file():
        raise FileNotFoundError("应用入口 Python 文件不存在")
    if not python_path.is_file():
        raise FileNotFoundError("应用 Python 环境不存在")
    return script, python_path


def runtime_ready(app: dict) -> bool:
    try:
        _managed_runtime_paths(app.get("script_path") or "", app.get("env_path") or "")
    except (ValueError, FileNotFoundError, RuntimeError, OSError):
        return False
    return True


def managed_runtime_paths(app: dict) -> tuple[Path, Path]:
    """Validate and return one app row's managed script and interpreter paths."""
    return _managed_runtime_paths(
        app.get("script_path") or "", app.get("env_path") or ""
    )


def active_environment_revision(app: dict) -> int | None:
    """Return the revision encoded in the currently published managed venv."""
    env_path = app.get("env_path") or ""
    if not env_path:
        return None
    try:
        env_dir = Path(env_path).expanduser().resolve()
        if env_dir.parent != RPA_ENVS_DIR.resolve():
            return None
    except OSError:
        return None
    match = _ENV_DIR_PATTERN.fullmatch(env_dir.name)
    if not match or int(match.group(1)) != int(app["id"]):
        return None
    return int(match.group(2))


def _build_environment_variables() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("SPIDERFLY_", "FEISHU_"))
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    environment["PIP_NO_INPUT"] = "1"
    return environment


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer: asyncio.subprocess.Process | None = None
        try:
            killer = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                ),
                timeout=3,
            )
            await asyncio.wait_for(killer.wait(), timeout=PROCESS_TERMINATION_SECONDS)
        except (OSError, asyncio.TimeoutError):
            if killer and killer.returncode is None:
                try:
                    killer.kill()
                except (OSError, ProcessLookupError):
                    pass
    else:
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=PROCESS_TERMINATION_SECONDS)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            logger.error("构建进程 PID %s 在终止预算内未确认退出", process.pid)


async def _run_command(
    *command: str,
    cwd: Path,
    timeout_seconds: int,
    phase: str,
) -> tuple[int, str]:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd),
        env=_build_environment_variables(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=creationflags,
    )
    communicate_task = asyncio.create_task(process.communicate())
    try:
        output, _ = await asyncio.wait_for(
            asyncio.shield(communicate_task), timeout=timeout_seconds
        )
    except asyncio.TimeoutError:
        await _terminate_process_tree(process)
        pipe_note = ""
        try:
            output, _ = await asyncio.wait_for(
                asyncio.shield(communicate_task), timeout=PIPE_DRAIN_SECONDS
            )
        except asyncio.TimeoutError:
            communicate_task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(communicate_task, return_exceptions=True), timeout=2
                )
            except asyncio.TimeoutError:
                logger.error("PID %s 的构建输出管道未在收尾预算内关闭", process.pid)
            output = b""
            pipe_note = f" 输出管道未在 {PIPE_DRAIN_SECONDS} 秒内关闭。"
        text = output.decode("utf-8", errors="replace")[-MAX_INSTALL_LOG_CHARS:]
        timeout_note = (
            f"[{phase}] 超过 {timeout_seconds} 秒，已请求终止本次构建进程树。{pipe_note}"
        )
        return 124, f"{text}\n{timeout_note}".strip()
    except asyncio.CancelledError:
        await _terminate_process_tree(process)
        communicate_task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(communicate_task, return_exceptions=True), timeout=2
            )
        except asyncio.TimeoutError:
            logger.error("取消构建时，PID %s 的输出管道未及时关闭", process.pid)
        raise
    text = output.decode("utf-8", errors="replace")[-MAX_INSTALL_LOG_CHARS:]
    return int(process.returncode if process.returncode is not None else -1), text


def _remove_candidate_environment(
    env_dir: Path,
    *,
    app_id: int,
    revision: int,
    active_env_path: str = "",
) -> bool:
    """Remove only an unpublished environment directory created by this build."""
    root = RPA_ENVS_DIR.resolve()
    lexical_candidate = env_dir.expanduser().absolute()
    is_junction = getattr(lexical_candidate, "is_junction", lambda: False)
    try:
        if lexical_candidate.is_symlink() or is_junction():
            return False
        candidate = lexical_candidate.resolve()
    except OSError:
        return False
    match = _ENV_DIR_PATTERN.fullmatch(candidate.name)
    if (
        candidate.parent != root
        or not match
        or int(match.group(1)) != app_id
        or int(match.group(2)) != revision
    ):
        return False
    if active_env_path:
        try:
            if Path(active_env_path).expanduser().resolve() == candidate:
                return False
        except OSError:
            return False
    if not candidate.exists():
        return True
    try:
        shutil.rmtree(candidate)
    except OSError:
        return False
    return True


async def build_environment(app_id: int) -> None:
    app = await asyncio.to_thread(
        fetch_one,
        "SELECT * FROM rpa_apps WHERE id = ? AND archived = 0",
        (app_id,),
    )
    if not app or app["environment_status"] != "pending":
        return
    revision = int(app["revision"])
    _, claimed = await asyncio.to_thread(
        execute_result,
        """
        UPDATE rpa_apps
        SET environment_status = 'building', environment_error = '', updated_at = ?
        WHERE id = ? AND revision = ? AND archived = 0
          AND environment_status = 'pending'
        """,
        (utc_now(), app_id, revision),
    )
    if claimed != 1:
        return

    app_dir = (RPA_APPS_DIR / str(app_id)).resolve()
    env_dir = (
        RPA_ENVS_DIR / f"app_{app_id}_r{revision}_{uuid.uuid4().hex[:8]}"
    ).resolve()
    logs: list[str] = []
    published = False
    try:
        expected_app_dir = (RPA_APPS_DIR / str(app_id)).resolve()
        if app_dir != expected_app_dir or app_dir.parent != RPA_APPS_DIR.resolve():
            raise RuntimeError("应用源码目录不在 SpiderFly 管理范围内")
        script_path = Path(app.get("script_path") or "").expanduser().resolve()
        if script_path.parent != app_dir:
            raise RuntimeError("应用入口文件不在当前应用目录内")
        validate_python_upload(script_path.name, script_path.read_bytes())
        requirements = _safe_requirements(app.get("requirements_text") or "")
        requirements_file = app_dir / "requirements.txt"
        requirements_file.write_text(
            requirements + ("\n" if requirements else ""),
            encoding="utf-8",
            newline="\n",
        )

        RPA_ENVS_DIR.mkdir(parents=True, exist_ok=True)
        base_python = Path(BASE_PYTHON).expanduser().resolve()
        if not base_python.is_file():
            raise FileNotFoundError(f"基础 Python 不存在：{base_python}")
        code, output = await _run_command(
            str(base_python),
            "-m",
            "venv",
            str(env_dir),
            cwd=app_dir,
            timeout_seconds=VENV_TIMEOUT_SECONDS,
            phase="创建虚拟环境",
        )
        logs.append(f"[创建虚拟环境]\n{output}".strip())
        if code != 0:
            if code == 124:
                raise RuntimeError(f"创建 Python 虚拟环境超过 {VENV_TIMEOUT_SECONDS} 秒")
            raise RuntimeError("创建 Python 虚拟环境失败")

        python_path = _environment_python(env_dir)
        if requirements:
            code, output = await _run_command(
                str(python_path),
                "-m",
                "pip",
                "install",
                "-r",
                str(requirements_file),
                cwd=app_dir,
                timeout_seconds=PIP_TIMEOUT_SECONDS,
                phase="安装依赖",
            )
            logs.append(f"[安装依赖]\n{output}".strip())
            if code != 0:
                if code == 124:
                    raise RuntimeError(f"安装依赖超过 {PIP_TIMEOUT_SECONDS} 秒")
                raise RuntimeError("安装 requirements.txt 失败")
        else:
            logs.append("[安装依赖]\n未声明第三方依赖，跳过安装。")

        if not python_path.is_file():
            raise FileNotFoundError("虚拟环境创建后未找到 Python 解释器")
        code, output = await _run_command(
            str(python_path),
            "-I",
            "-c",
            "import sys; print(sys.executable); print(sys.prefix)",
            cwd=app_dir,
            timeout_seconds=ENV_VERIFY_TIMEOUT_SECONDS,
            phase="验证解释器",
        )
        logs.append(f"[验证解释器]\n{output}".strip())
        if code != 0:
            if code == 124:
                raise RuntimeError(f"验证解释器超过 {ENV_VERIFY_TIMEOUT_SECONDS} 秒")
            raise RuntimeError("新建 Python 解释器无法正常启动")

        code, output = await _run_command(
            str(python_path),
            "-m",
            "pip",
            "check",
            cwd=app_dir,
            timeout_seconds=ENV_VERIFY_TIMEOUT_SECONDS,
            phase="检查依赖",
        )
        logs.append(f"[检查依赖]\n{output}".strip())
        if code != 0:
            if code == 124:
                raise RuntimeError(f"检查依赖超过 {ENV_VERIFY_TIMEOUT_SECONDS} 秒")
            raise RuntimeError("Python 依赖检查未通过")

        _, updated = await asyncio.to_thread(
            execute_result,
            """
            UPDATE rpa_apps
            SET env_path = ?, environment_status = 'ready', environment_error = '',
                install_log = ?, updated_at = ?
            WHERE id = ? AND revision = ? AND archived = 0
              AND environment_status = 'building'
            """,
            (
                str(env_dir),
                "\n\n".join(logs)[-MAX_INSTALL_LOG_CHARS:],
                utc_now(),
                app_id,
                revision,
            ),
        )
        if updated != 1:
            return
        published = True
        try:
            active_rows = await asyncio.to_thread(
                fetch_all,
                """
                SELECT e.python_path_snapshot
                FROM executions e
                JOIN tasks t ON t.id = e.task_id
                WHERE t.app_id = ? AND e.status IN ('pending', 'running')
                """,
                (app_id,),
            )
            await asyncio.to_thread(
                cleanup_old_environments,
                app_id,
                str(env_dir),
                tuple(
                    str(item.get("python_path_snapshot") or "")
                    for item in active_rows
                ),
            )
        except Exception:
            logger.exception("应用 %s 已发布，但旧 Python 环境未能完全清理", app_id)
    except asyncio.CancelledError:
        await asyncio.to_thread(
            execute,
            """
            UPDATE rpa_apps
            SET environment_status = 'pending', environment_error = '服务停止，等待重新构建',
                install_log = ?, updated_at = ?
            WHERE id = ? AND revision = ? AND archived = 0
              AND environment_status = 'building'
            """,
            ("\n\n".join(logs)[-MAX_INSTALL_LOG_CHARS:], utc_now(), app_id, revision),
        )
        raise
    except Exception as exc:
        await asyncio.to_thread(
            execute,
            """
            UPDATE rpa_apps
            SET environment_status = 'failed', environment_error = ?,
                install_log = ?, updated_at = ?
            WHERE id = ? AND revision = ? AND archived = 0
              AND environment_status = 'building'
            """,
            (
                str(exc)[:1000],
                "\n\n".join(logs)[-MAX_INSTALL_LOG_CHARS:],
                utc_now(),
                app_id,
                revision,
            ),
        )
    finally:
        if not published:
            try:
                current = await asyncio.to_thread(
                    fetch_one, "SELECT env_path FROM rpa_apps WHERE id = ?", (app_id,)
                )
                await asyncio.to_thread(
                    _remove_candidate_environment,
                    env_dir,
                    app_id=app_id,
                    revision=revision,
                    active_env_path=(current or {}).get("env_path") or "",
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("清理应用 %s 的未发布候选环境失败", app_id)


def app_runtime(app_id: int) -> tuple[dict, Path, Path]:
    app = fetch_one(
        "SELECT * FROM rpa_apps WHERE id = ? AND archived = 0", (app_id,)
    )
    if not app:
        raise FileNotFoundError("自动化程序不存在或已经移除")
    try:
        script, python_path = managed_runtime_paths(app)
    except (FileNotFoundError, RuntimeError) as exc:
        if app["environment_status"] != "ready":
            raise RuntimeError("Python 应用环境尚未就绪") from exc
        raise
    return app, script, python_path
