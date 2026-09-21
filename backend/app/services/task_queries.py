"""任务查询、公开字段与计划参数转换。"""

from __future__ import annotations

import json
from fastapi import HTTPException
from ..database import fetch_one
from ..environments import active_environment_revision, runtime_ready
from ..scheduling import (
    compute_next_run,
    decode_trigger_config,
    encode_trigger_config,
    normalize_trigger,
)


def validate_target_host(conn, host_id: int | None) -> None:
    if host_id is None:
        return
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_hosts'"
    ).fetchone()
    if not exists:
        raise HTTPException(status_code=400, detail="宿主机功能尚未初始化")
    host = conn.execute(
        "SELECT approval_status FROM agent_hosts WHERE id=?", (host_id,)
    ).fetchone()
    if not host:
        raise HTTPException(status_code=400, detail="计划运行宿主机不存在")
    if host["approval_status"] != "approved":
        raise HTTPException(status_code=409, detail="计划运行宿主机尚未批准或已撤销")


TASK_SELECT = """
    SELECT
        t.*,
        a.script_filename,
        a.script_path AS app_script_path,
        a.env_path AS app_env_path,
        a.environment_status,
        a.environment_error,
        a.revision AS app_revision,
        creator.display_name AS created_by_name,
        updater.display_name AS updated_by_name
    FROM tasks t
    LEFT JOIN rpa_apps a ON a.id = t.app_id
    LEFT JOIN users creator ON creator.id = t.created_by
    LEFT JOIN users updater ON updater.id = t.updated_by
"""


def _public_task(task: dict) -> dict:
    item = dict(task)
    item["trigger_config"] = decode_trigger_config(item.get("trigger_config"))
    item["runtime_ready"] = runtime_ready(
        {
            "script_path": item.get("app_script_path") or item.get("script_path") or "",
            "env_path": item.get("app_env_path") or "",
        }
    )
    for key in ("enabled", "notify_on_success", "notify_on_failure", "failure_screenshot", "archived"):
        item[key] = bool(item.get(key))
    for key in ("script_path", "python_path", "app_script_path", "app_env_path"):
        item.pop(key, None)
    item["target_host_name"] = ""
    if item.get("target_host_id") and fetch_one(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_hosts'"
    ):
        host = fetch_one("SELECT name FROM agent_hosts WHERE id=?", (item["target_host_id"],))
        item["target_host_name"] = host["name"] if host else "宿主机已移除"
    return item


def _task_or_404(task_id: int, *, public: bool = False) -> dict:
    task = fetch_one(
        f"{TASK_SELECT} WHERE t.id = ? AND t.archived = 0", (task_id,)
    )
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _public_task(task) if public else task


def _public_app(item: dict, *, include_private: bool = False) -> dict:
    result = {
        "id": int(item["id"]),
        "name": item["name"],
        "script_filename": item["script_filename"],
        "template_filename": item.get("template_filename") or "",
        "environment_status": item["environment_status"],
        "environment_error": item.get("environment_error") or "",
        "runtime_ready": runtime_ready(item),
        "revision": int(item.get("revision") or 1),
        "requested_revision": int(item.get("revision") or 1),
        "active_revision": active_environment_revision(item),
        "active_task_count": int(item.get("active_task_count") or 0),
        "archived_task_count": int(item.get("archived_task_count") or 0),
        "created_by_name": item.get("created_by_name") or "系统迁移",
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }
    if include_private:
        result["requirements_text"] = item.get("requirements_text") or ""
        result["install_log"] = item.get("install_log") or ""
    return result


def _schedule_values(
    trigger_type: str, trigger_config: dict, enabled: bool
) -> tuple[str, str | None]:
    normalized = normalize_trigger(trigger_type, trigger_config)
    next_run = compute_next_run(trigger_type, normalized) if enabled else None
    if enabled and trigger_type == "once" and next_run is None:
        raise ValueError("单次执行时间必须晚于当前时间")
    return encode_trigger_config(normalized), next_run


def _create_task_schedule_values(
    description: str,
    trigger_type: str,
    trigger_config: str,
    enabled: bool,
) -> tuple[str, str, str, str | None]:
    clean_description = description.strip()
    if len(clean_description) > 500:
        raise ValueError("任务说明不能超过 500 个字符")
    trigger_type = (trigger_type or "manual").strip().lower()
    if trigger_type not in {"manual", "daily", "weekly"}:
        raise ValueError("不支持的触发方式")
    try:
        config = json.loads((trigger_config or "{}").strip() or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("运行时间设置格式错误") from exc
    if not isinstance(config, dict):
        raise ValueError("运行时间设置必须是一个对象")
    encoded_config, next_run_at = _schedule_values(
        trigger_type, config, bool(enabled)
    )
    return clean_description, trigger_type, encoded_config, next_run_at
