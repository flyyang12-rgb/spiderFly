"""Manual local workflows: immutable rounds and cleanup-gated serial progress.

Connection-taking helpers never commit. Definitions and history deliberately do
not cascade with task/execution deletion; only an active current step occupies a
task, while every unfinished round protects all of its frozen environments.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import logging
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from fastapi import HTTPException

from ..config import DATA_DIR, DEFAULT_TASK_TIMEOUT_SECONDS, MANAGED_BROWSER_PORT, RPA_APPS_DIR, RPA_ENVS_DIR
from ..database import TASK_EXECUTION_STATUS_SQL, transaction, utc_now

logger = logging.getLogger(__name__)
SNAPSHOT_ROOT = DATA_DIR / "workflow_snapshots"
ACTIVE = ("waiting", "running", "stopping")
TERMINAL = {"success", "failed", "timeout", "cancelled"}
MAX_STEPS = 100


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def has_schema(conn):
    return _table(conn, "workflow_runs")


def ensure_schema(conn):
    # Do not use executescript: it would commit an enclosing acceptance transaction.
    statements = (
        """CREATE TABLE IF NOT EXISTS workflows (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
            archived INTEGER NOT NULL DEFAULT 0, trigger_type TEXT NOT NULL DEFAULT 'manual',
            trigger_config TEXT NOT NULL DEFAULT '{}', schedule_enabled INTEGER NOT NULL DEFAULT 0,
            next_run_at TEXT, created_by INTEGER, updated_by INTEGER,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS workflow_members (
            workflow_id INTEGER NOT NULL, position INTEGER NOT NULL, task_id INTEGER NOT NULL,
            PRIMARY KEY(workflow_id,position), UNIQUE(workflow_id,task_id))""",
        """CREATE TABLE IF NOT EXISTS workflow_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id INTEGER NOT NULL,
            workflow_name TEXT NOT NULL, definition_revision INTEGER NOT NULL,
            requested_by INTEGER, requested_by_name TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual', request_key TEXT NOT NULL UNIQUE,
            request_intent TEXT NOT NULL, rerun_of INTEGER, scheduled_for TEXT,
            state TEXT NOT NULL CHECK(state IN ('waiting','running','stopping','success','failed','cancelled')),
            stop_requested INTEGER NOT NULL DEFAULT 0, timeout_seconds INTEGER NOT NULL,
            current_step INTEGER, reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, started_at TEXT, ended_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS workflow_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
            position INTEGER NOT NULL, task_id INTEGER NOT NULL, app_id INTEGER NOT NULL,
            task_name TEXT NOT NULL, task_version INTEGER NOT NULL,
            snapshot TEXT NOT NULL, snapshot_digest TEXT NOT NULL,
            snapshot_storage_dir TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'waiting', execution_id INTEGER UNIQUE,
            started_at TEXT, ended_at TEXT, error_summary TEXT NOT NULL DEFAULT '',
            UNIQUE(run_id,position), UNIQUE(run_id,task_id))""",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_active ON workflow_runs(workflow_id) WHERE state IN ('waiting','running','stopping')",
        "CREATE INDEX IF NOT EXISTS ix_workflow_history ON workflow_runs(workflow_id,id DESC)",
        "CREATE INDEX IF NOT EXISTS ix_workflow_step_task ON workflow_steps(task_id,run_id)",
    )
    for statement in statements:
        conn.execute(statement)
    columns = {item["name"] for item in conn.execute("PRAGMA table_info(workflow_steps)")}
    if "snapshot_storage_dir" not in columns:
        conn.execute("ALTER TABLE workflow_steps ADD COLUMN snapshot_storage_dir TEXT NOT NULL DEFAULT ''")
    from . import workflow_schedules
    workflow_schedules.ensure_schema(conn)


@contextmanager
def _connection(conn=None) -> Iterator[sqlite3.Connection]:
    if conn is not None:
        yield conn
    else:
        with transaction() as opened:
            yield opened


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _definition(conn, workflow_id, *, active=False):
    item = conn.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
    if not item or (active and item["archived"]):
        raise HTTPException(404, "工作流不存在或已归档")
    return dict(item)


def _run(conn, run_id):
    item = conn.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
    if not item:
        raise HTTPException(404, "流程运行不存在")
    return dict(item)


def _member(conn, task_id):
    item = conn.execute("""SELECT t.*,a.script_path AS app_script_path,
        a.requirements_text,a.env_path,a.environment_status,a.archived AS app_archived,
        a.template_path,a.template_filename,a.revision AS app_revision
        FROM tasks t LEFT JOIN rpa_apps a ON a.id=t.app_id WHERE t.id=?""", (task_id,)).fetchone()
    if not item or item["archived"]:
        raise HTTPException(409, f"步骤任务 {task_id} 不存在，请修正工作流")
    task = dict(item)
    if not task.get("app_id") or task.get("app_archived") is None or task["app_archived"]:
        raise HTTPException(409, f"任务“{task['name']}”的程序不存在")
    return task


def _native_runtime(conn, task, source=None):
    runtime = None
    if _table(conn, "maintenance_policies"):
        policy = conn.execute("SELECT runtime FROM maintenance_policies WHERE task_id=?", (task["id"],)).fetchone()
        if policy:
            runtime = policy["runtime"]
    if runtime is None:
        if source is None:
            path = _managed_file(task["app_script_path"], RPA_APPS_DIR, 2 * 1024 * 1024, "任务源码")
            source = path.read_text("utf-8-sig")
        from ..collection import uses_collection
        from ..dp_runtime import uses_dp
        runtime = ("drissionpage-v1" if uses_dp(source) else "collection-v1") if uses_collection(source) else "native"
    if runtime != "native":
        raise HTTPException(409, f"任务“{task['name']}”使用 {runtime}，手动流程首版只支持普通 Python 任务")
    return runtime


def _validate_members(conn, task_ids):
    if not isinstance(task_ids, (list, tuple)) or not 1 <= len(task_ids) <= MAX_STEPS:
        raise HTTPException(400, f"工作流需要 1 到 {MAX_STEPS} 个步骤")
    if any(isinstance(i, bool) or not isinstance(i, int) or i < 1 for i in task_ids):
        raise HTTPException(400, "步骤任务编号无效")
    if len(set(task_ids)) != len(task_ids):
        raise HTTPException(400, "工作流不能重复引用同一任务")
    for task_id in task_ids:
        task = _member(conn, task_id)
        if task.get("target_host_id") is not None:
            raise HTTPException(409, f"任务“{task['name']}”绑定了远程电脑，流程只支持主控本机")
        _native_runtime(conn, task)


def _text(name, description):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        raise HTTPException(400, "工作流名称需要 1 到 100 个字符")
    if not isinstance(description, str) or len(description.strip()) > 1000:
        raise HTTPException(400, "工作流说明不能超过 1000 个字符")
    return name.strip(), description.strip()


def create_definition(conn, name, description, task_ids, actor=None, *, schedule=None):
    name, description = _text(name, description)
    _validate_members(conn, task_ids)
    now = utc_now()
    try:
        workflow_id = int(conn.execute("""INSERT INTO workflows(name,description,created_by,updated_by,created_at,updated_at)
            VALUES(?,?,?,?,?,?)""", (name, description, actor, actor, now, now)).lastrowid)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "工作流名称已存在") from exc
    conn.executemany("INSERT INTO workflow_members VALUES(?,?,?)", [(workflow_id, i, task_id) for i, task_id in enumerate(task_ids, 1)])
    if schedule is not None:
        from .workflow_schedules import update_plan
        return update_plan(conn, workflow_id, schedule['trigger_type'], schedule['trigger_config'],
                           schedule['schedule_enabled'], 1, actor, bump_revision=False)
    return get_definition(conn, workflow_id)


def update_definition(conn, workflow_id, name, description, task_ids, actor=None, *, expected_revision, schedule=None):
    previous = _definition(conn, workflow_id, active=True)
    if previous["revision"] != expected_revision:
        raise HTTPException(409, "工作流已被其他操作修改，请刷新后重试")
    name, description = _text(name, description)
    _validate_members(conn, task_ids)
    if previous['schedule_enabled'] and (schedule is None or schedule['schedule_enabled']):
        from .workflow_schedules import validate_members
        validate_members(conn, task_ids)
    try:
        conn.execute("""UPDATE workflows SET name=?,description=?,revision=revision+1,updated_by=?,updated_at=?
            WHERE id=? AND revision=?""", (name, description, actor, utc_now(), workflow_id, expected_revision))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "工作流名称已存在") from exc
    conn.execute("DELETE FROM workflow_members WHERE workflow_id=?", (workflow_id,))
    conn.executemany("INSERT INTO workflow_members VALUES(?,?,?)", [(workflow_id, i, task_id) for i, task_id in enumerate(task_ids, 1)])
    if schedule is not None:
        from .workflow_schedules import update_plan
        return update_plan(conn, workflow_id, schedule['trigger_type'], schedule['trigger_config'],
                           schedule['schedule_enabled'], expected_revision + 1, actor, bump_revision=False)
    return get_definition(conn, workflow_id)


def archive_definition(conn, workflow_id, actor=None):
    _definition(conn, workflow_id, active=True)
    if conn.execute("SELECT 1 FROM workflow_runs WHERE workflow_id=? AND state IN ('waiting','running','stopping')", (workflow_id,)).fetchone():
        raise HTTPException(409, "工作流仍有未结束运行，请先停止并确认收尾")
    conn.execute("UPDATE workflows SET archived=1,schedule_enabled=0,next_run_at=NULL,revision=revision+1,updated_by=?,updated_at=? WHERE id=?", (actor, utc_now(), workflow_id))
    return get_definition(conn, workflow_id)


def get_definition(conn, workflow_id):
    result = _definition(conn, workflow_id)
    result["archived"] = bool(result["archived"])
    result["schedule_enabled"] = bool(result["schedule_enabled"])
    result['trigger_config'] = json.loads(result['trigger_config'] or '{}')
    result["steps"] = [dict(item) for item in conn.execute("""SELECT m.position,m.task_id,
        COALESCE(t.name,'任务已删除') AS task_name,
        CASE WHEN t.id IS NOT NULL AND t.archived=0 THEN 1 ELSE 0 END AS available
        FROM workflow_members m LEFT JOIN tasks t ON t.id=m.task_id WHERE m.workflow_id=? ORDER BY m.position""", (workflow_id,))]
    for step in result["steps"]:
        step["available"] = bool(step["available"])
    active = conn.execute("SELECT id FROM workflow_runs WHERE workflow_id=? AND state IN ('waiting','running','stopping')", (workflow_id,)).fetchone()
    result["active_run_id"] = int(active["id"]) if active else None
    last = conn.execute("SELECT id FROM workflow_runs WHERE workflow_id=? ORDER BY id DESC LIMIT 1", (workflow_id,)).fetchone()
    result["last_run"] = get_run(conn, last["id"], include_steps=False) if last else None
    return result


def list_definitions(conn, include_archived=False):
    condition = "" if include_archived else " WHERE archived=0"
    return [get_definition(conn, item["id"]) for item in conn.execute("SELECT id FROM workflows" + condition + " ORDER BY id DESC")]


def list_task_candidates(conn):
    result = []
    for item in conn.execute("SELECT id,name,enabled,target_host_id FROM tasks WHERE archived=0 ORDER BY name"):
        candidate = dict(item)
        candidate["enabled"] = bool(candidate["enabled"])
        reason = ""
        try:
            task = _member(conn, item["id"])
            if task["target_host_id"] is not None:
                reason = "绑定远程电脑，流程首版只支持主控本机"
            else:
                _native_runtime(conn, task)
                if not task["enabled"]:
                    reason = "任务已停用，启用后才能启动流程"
                elif task["environment_status"] != "ready" or not task.get('first_environment_ready', 1):
                    reason = "Python 环境尚未准备好"
        except HTTPException as exc:
            reason = str(exc.detail)
        except (ValueError, OSError) as exc:
            reason = str(exc)
        candidate["available"] = not bool(reason)
        candidate["reason"] = reason
        candidate["eligible"] = candidate["available"]
        candidate["unavailable_reason"] = reason
        candidate["runtime"] = "native" if not reason or "已停用" in reason or "环境尚未" in reason else ""
        result.append(candidate)
    return result


def get_run(conn, run_id, *, include_steps=True):
    result = _run(conn, run_id)
    active = result["state"] in ACTIVE
    result["status"] = result.pop("state")
    result["stop_requested"] = bool(result["stop_requested"])
    result.pop("request_key", None)
    result.pop("request_intent", None)
    proof_join = "LEFT JOIN execution_settlements proof ON proof.execution_id=e.id" if _table(conn, "execution_settlements") else ""
    proof_column = "COALESCE(proof.cleanup_confirmed,0)" if proof_join else "0"
    rows = conn.execute(f"""SELECT s.*,
        CASE WHEN e.id IS NOT NULL AND t.id IS NOT NULL THEN 1 ELSE 0 END AS execution_available,
        e.task_id AS live_task_id,e.status AS live_status,e.started_at AS live_started_at,
        e.ended_at AS live_ended_at,e.error_message AS live_error,
        {proof_column} AS live_cleanup_confirmed
        FROM workflow_steps s LEFT JOIN executions e ON e.id=s.execution_id
        LEFT JOIN tasks t ON t.id=s.task_id {proof_join}
        WHERE s.run_id=? ORDER BY s.position""", (run_id,)).fetchall()
    public_steps = []
    for item in rows:
        step = dict(item)
        live = {key.removeprefix("live_"): step.pop(key) for key in tuple(step) if key.startswith("live_")}
        step["status"] = step.pop("state")
        step["execution_available"] = bool(step["execution_available"])
        linked = live["task_id"] == step["task_id"]
        if linked and live["started_at"]:
            step["started_at"] = step["started_at"] or live["started_at"]
        if result["started_at"] is None and step["started_at"]:
            result["started_at"] = step["started_at"]
        # The worker awaits the runner. Reads can reflect its current execution
        # without advancing this round or manufacturing a cleanup confirmation.
        if active and linked and step["position"] == result["current_step"] and step["status"] in {"waiting", "pending", "running", "stopping"}:
            live_status = live["status"]
            if live_status in {"pending", "running"}:
                step["status"] = "stopping" if result["stop_requested"] else live_status
                if not result["stop_requested"]:
                    step["error_summary"] = (live["error"] or "")[:1000] if live_status == "pending" else ""
                    result["reason"] = step["error_summary"]
                    if live_status == "running":
                        result["status"] = "running"
            elif live_status in TERMINAL:
                if live["cleanup_confirmed"]:
                    step["status"] = live_status
                    step["ended_at"] = live["ended_at"] or step["ended_at"]
                    step["error_summary"] = (live["error"] or "")[:1000]
                    if result["stop_requested"]:
                        result["status"] = "stopping"
                else:
                    step["status"] = "stopping" if result["stop_requested"] else "waiting"
                    wait = "等待步骤进程树、公共工作区和执行器返回确认"
                    step["error_summary"] = "；".join(text for text in ((live["error"] or "")[:1000], wait) if text)
                    if not result["stop_requested"]:
                        result["reason"] = wait
        for private in ("snapshot", "snapshot_digest", "snapshot_storage_dir", "app_id"):
            step.pop(private, None)
        public_steps.append(step)
    if include_steps:
        result["steps"] = public_steps
    return result


def list_runs(conn, workflow_id=None, status=None, date_from=None, date_to=None, page=1, page_size=30):
    page, page_size = max(1, int(page)), min(100, max(1, int(page_size)))
    conditions, parameters = [], []
    for column, value, operation in (("workflow_id", workflow_id, "="), ("state", status, "="), ("created_at", date_from, ">="), ("created_at", date_to, "<")):
        if value is not None and value != "":
            conditions.append(f"{column}{operation}?")
            parameters.append(value)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    count = conn.execute("SELECT COUNT(*) FROM workflow_runs" + where, parameters).fetchone()[0]
    ids = conn.execute("SELECT id FROM workflow_runs" + where + " ORDER BY id DESC LIMIT ? OFFSET ?", (*parameters, page_size, (page-1)*page_size)).fetchall()
    return {"items": [get_run(conn, item["id"], include_steps=False) for item in ids], "total": count, "page": page, "page_size": page_size}


def _managed_path(value, root, label):
    raw, managed_root = Path(value or "").expanduser().absolute(), root.absolute()
    try:
        relative = raw.relative_to(managed_root)
    except ValueError as exc:
        raise ValueError(f"{label}不在管理目录") from exc
    cursor = managed_root
    # Check before resolve: resolving a link would erase the evidence of it.
    for part in ("", *relative.parts):
        cursor = cursor / part
        if cursor.is_symlink() or cursor.is_junction():
            raise ValueError(f"{label}路径包含符号链接或目录联接")
    path = raw.resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"{label}不在管理目录")
    return path


def _managed_file(value, root, max_size, label):
    path = _managed_path(value, root, label)
    if not path.is_file() or path.stat().st_size > max_size:
        raise ValueError(f"{label}不存在、超出大小限制或不在管理目录")
    return path


def _capture_step(conn, task_id, position, folder, timeout):
    task = _member(conn, task_id)
    if not task["enabled"]:
        raise HTTPException(409, f"任务“{task['name']}”已停用")
    if task["target_host_id"] is not None:
        raise HTTPException(409, f"任务“{task['name']}”绑定远程电脑")
    if task["environment_status"] != "ready" or not task.get('first_environment_ready', 1):
        raise HTTPException(409, f"任务“{task['name']}”的 Python 环境尚未准备好")
    from ..environments import managed_runtime_paths
    script, interpreter = managed_runtime_paths({"script_path": task["app_script_path"], "env_path": task["env_path"]})
    source_data = _managed_file(str(script), RPA_APPS_DIR, 2 * 1024 * 1024, "任务源码").read_bytes()
    # Version sources use Path.read_text's universal-newline convention. Keep
    # the original frozen bytes, but compare textual versions on that same basis.
    source = source_data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    _native_runtime(conn, task, source)
    # tasks.version is a configuration revision, not a code version sequence.
    version = 1
    if _table(conn, "maintenance_policies") and _table(conn, "task_code_versions"):
        policy_version = conn.execute("SELECT active_version_id FROM maintenance_policies WHERE task_id=?", (task_id,)).fetchone()
        active = conn.execute("""SELECT v.sequence,v.source,v.requirements FROM maintenance_policies p
            JOIN task_code_versions v ON v.id=p.active_version_id WHERE p.task_id=? AND v.task_id=p.task_id""", (task_id,)).fetchone()
        if policy_version and policy_version["active_version_id"] is not None and not active:
            raise ValueError(f"任务“{task['name']}”已启用版本关联缺失，请先确认版本")
        if active:
            if active["source"] != source or active["requirements"] != task["requirements_text"]:
                raise ValueError(f"任务“{task['name']}”当前源码与已启用版本不一致，请先确认版本")
            version = int(active["sequence"])
    folder.mkdir(parents=True, exist_ok=False)
    frozen_script = folder / "main.py"
    with frozen_script.open("xb") as stream:
        stream.write(source_data)
    template_path, template_hash, template_name = "", "", task.get("template_filename") or ""
    if task.get("template_path"):
        template = _managed_file(task["template_path"], RPA_APPS_DIR, 64 * 1024 * 1024, "任务模板")
        data = template.read_bytes()
        frozen_template = folder / ("template" + template.suffix.lower())
        with frozen_template.open("xb") as stream:
            stream.write(data)
        template_path, template_hash = str(frozen_template), _digest(data)
    snapshot = {"id": task_id, "name": task["name"], "description": task.get("description") or "",
        "app_id": int(task["app_id"]), "task_version": version, "position": position,
        "task_revision": int(task.get("version") or 1), "app_revision": int(task["app_revision"]),
        "script_path_snapshot": str(frozen_script), "script_sha256": _digest(source_data),
        "python_path_snapshot": str(interpreter), "runtime": "native", "target_host_id": None,
        "requirements_text": task["requirements_text"] or "", "template_path": template_path,
        "template_sha256": template_hash, "template_filename": template_name,
        "notify_on_success": bool(task["notify_on_success"]), "notify_on_failure": bool(task["notify_on_failure"]),
        "failure_screenshot": bool(task["failure_screenshot"]), "timeout_seconds": timeout,
        "browser_port": MANAGED_BROWSER_PORT}
    _verify_snapshot(snapshot)
    return snapshot


def _verify_snapshot(snapshot):
    if snapshot.get("runtime") != "native" or snapshot.get("target_host_id") is not None:
        raise ValueError("流程快照运行参数无效")
    script = _managed_file(snapshot.get("script_path_snapshot"), SNAPSHOT_ROOT, 2 * 1024 * 1024, "冻结源码")
    if _digest(script.read_bytes()) != snapshot.get("script_sha256"):
        raise ValueError("冻结源码哈希不一致")
    interpreter = _managed_file(snapshot.get("python_path_snapshot"), RPA_ENVS_DIR, 64 * 1024 * 1024, "冻结 Python 环境")
    if snapshot.get("template_path"):
        template = _managed_file(snapshot["template_path"], SNAPSHOT_ROOT, 64 * 1024 * 1024, "冻结模板")
        if template.parent != script.parent or _digest(template.read_bytes()) != snapshot.get("template_sha256"):
            raise ValueError("冻结模板哈希不一致")
    if isinstance(snapshot.get("timeout_seconds"), bool) or not isinstance(snapshot.get("timeout_seconds"), int) or not 60 <= snapshot["timeout_seconds"] <= 86400:
        raise ValueError("冻结超时参数无效")
    if isinstance(snapshot.get("browser_port"), bool) or not isinstance(snapshot.get("browser_port"), int) or not 1024 <= snapshot["browser_port"] <= 65535:
        raise ValueError("冻结浏览器端口缺失或无效")
    return str(interpreter)


def _identity(workflow_id, requested_by, request_id, intent, rerun_of):
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
        raise HTTPException(400, "请求标识必须为 1 到 200 字符")
    operation = "rerun" if rerun_of is not None else "start"
    key = _json([requested_by, workflow_id, operation, request_id])
    encoded = _json({"operation": operation, "workflow_id": workflow_id, "requested_by": requested_by, "rerun_of": rerun_of, "intent": intent or {}})
    return key, encoded


def start_run(workflow_id, requested_by, request_id, intent=None, rerun_of=None, *, conn=None,
              source='manual', scheduled_for=None):
    owns_transaction = conn is None
    key, encoded = _identity(workflow_id, requested_by, request_id, intent, rerun_of)
    folder = None
    accepted = False
    try:
        with _connection(conn) as conn:
            previous = conn.execute("SELECT id,request_intent FROM workflow_runs WHERE request_key=?", (key,)).fetchone()
            if previous:
                if previous["request_intent"] != encoded:
                    raise HTTPException(409, "请求标识已用于不同的流程运行意图")
                return get_run(conn, previous["id"])
            definition = _definition(conn, workflow_id, active=True)
            if rerun_of is not None:
                old = _run(conn, rerun_of)
                if old["workflow_id"] != workflow_id or old["state"] in ACTIVE:
                    raise HTTPException(409, "只能整轮重跑这个工作流的已结束记录")
            if conn.execute("SELECT 1 FROM workflow_runs WHERE workflow_id=? AND state IN ('waiting','running','stopping')", (workflow_id,)).fetchone():
                raise HTTPException(409, "工作流已有一轮未结束运行")
            members = conn.execute("SELECT position,task_id FROM workflow_members WHERE workflow_id=? ORDER BY position", (workflow_id,)).fetchall()
            if not members:
                raise HTTPException(409, "工作流没有步骤")
            timeout = int(DEFAULT_TASK_TIMEOUT_SECONDS)
            folder = SNAPSHOT_ROOT / uuid4().hex
            folder.mkdir(parents=True, exist_ok=False)
            # Files precede the runnable round; all member revisions are protected
            # by this BEGIN IMMEDIATE until acceptance is committed.
            snapshots = [_capture_step(conn, item["task_id"], item["position"], folder / str(item["position"]), timeout) for item in members]
            now = utc_now()
            user = conn.execute("SELECT display_name FROM users WHERE id=?", (requested_by,)).fetchone() if _table(conn, "users") else None
            run_id = int(conn.execute("""INSERT INTO workflow_runs(workflow_id,workflow_name,definition_revision,requested_by,
                requested_by_name,request_key,request_intent,rerun_of,state,timeout_seconds,current_step,created_at,source,scheduled_for)
                VALUES(?,?,?,?,?,?,?,?,'waiting',?,1,?,?,?)""", (workflow_id, definition["name"], definition["revision"], requested_by,
                user["display_name"] if user else "", key, encoded, rerun_of, timeout, now, source, scheduled_for)).lastrowid)
            for snapshot in snapshots:
                saved = _json(snapshot)
                conn.execute("""INSERT INTO workflow_steps(run_id,position,task_id,app_id,task_name,task_version,snapshot,snapshot_digest,snapshot_storage_dir)
                    VALUES(?,?,?,?,?,?,?,?,?)""", (run_id, snapshot["position"], snapshot["id"], snapshot["app_id"], snapshot["name"], snapshot["task_version"], saved, _digest(saved.encode()), str(Path(snapshot["script_path_snapshot"]).parent)))
            _coordinate(conn, run_id)
            result = get_run(conn, run_id)
        accepted = True
        if owns_transaction:
            logger.info("工作流 %s 接受运行 %s，共 %s 步", workflow_id, run_id, len(snapshots))
        return result
    finally:
        if folder is not None and not accepted:
            # Random preparation directory beneath our own root only.
            if folder.resolve().parent == SNAPSHOT_ROOT.resolve() and folder.is_dir():
                shutil.rmtree(folder)


def execution_step(conn, execution_id):
    if not has_schema(conn):
        return None
    item = conn.execute("SELECT * FROM workflow_steps WHERE execution_id=?", (execution_id,)).fetchone()
    return dict(item) if item else None


def execution_snapshot(conn, execution_id):
    step = execution_step(conn, execution_id)
    if not step:
        return None
    try:
        if _digest(step["snapshot"].encode()) != step["snapshot_digest"]:
            raise ValueError("流程快照清单哈希不一致")
        snapshot = json.loads(step["snapshot"])
        if not isinstance(snapshot, dict) or snapshot.get("id") != step["task_id"] or snapshot.get("position") != step["position"]:
            raise ValueError("流程快照关联无效")
        _verify_snapshot(snapshot)
        return snapshot
    except (KeyError, TypeError, json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"流程冻结快照缺失或损坏：{exc}") from exc


def assert_execution_allowed(conn, execution_id):
    step = execution_step(conn, execution_id)
    if not step:
        return
    run = _run(conn, step["run_id"])
    if run["stop_requested"] or run["state"] not in ACTIVE:
        raise InterruptedError(run["reason"] or "流程已请求停止")
    task = conn.execute("SELECT enabled,archived FROM tasks WHERE id=?", (step["task_id"],)).fetchone()
    if not task or task["archived"]:
        raise InterruptedError(f"步骤任务“{step['task_name']}”已删除")
    if not task["enabled"]:
        raise InterruptedError(f"步骤任务“{step['task_name']}”已停用")


def active_task_reference(conn, task_id):
    return bool(has_schema(conn) and conn.execute("""SELECT 1 FROM workflow_steps s JOIN workflow_runs r ON r.id=s.run_id
        WHERE s.task_id=? AND r.state IN ('waiting','running','stopping') LIMIT 1""", (task_id,)).fetchone())


def protected_python_paths(conn, app_id=None):
    if not has_schema(conn):
        return []
    condition, args = (" AND s.app_id=?", (app_id,)) if app_id is not None else ("", ())
    paths = []
    for item in conn.execute("""SELECT s.* FROM workflow_steps s JOIN workflow_runs r ON r.id=s.run_id
        WHERE r.state IN ('waiting','running','stopping')""" + condition, args):
        try:
            if _digest(item["snapshot"].encode()) != item["snapshot_digest"]:
                raise ValueError("清单哈希不一致")
            snapshot = json.loads(item["snapshot"])
            if snapshot["id"] != item["task_id"] or snapshot["app_id"] != item["app_id"]:
                raise ValueError("关联不一致")
            _verify_snapshot(snapshot)
            path = snapshot["python_path_snapshot"]
        except (ValueError, KeyError, TypeError, OSError) as exc:
            # Fail closed: a damaged reference is not permission to delete an env.
            raise RuntimeError("活动流程的环境引用损坏，已停止环境清理") from exc
        paths.append(path)
    return list(dict.fromkeys(paths))


def task_busy_reason(conn, task_id, workflow_only=False):
    if workflow_only:
        if not has_schema(conn):
            return ""
        rows = conn.execute("""SELECT s.execution_id,e.status FROM workflow_steps s
            JOIN workflow_runs r ON r.id=s.run_id LEFT JOIN executions e ON e.id=s.execution_id
            WHERE s.task_id=? AND s.execution_id IS NOT NULL AND r.state IN ('waiting','running','stopping')""", (task_id,)).fetchall()
        for row in rows:
            if row["status"] in {"pending", "running"}:
                return "同任务工作流步骤已排队或运行"
            if row["status"] is None or not _confirmed(conn, row["execution_id"]):
                return "同任务工作流步骤收尾尚未确认"
        return ""
    if _table(conn, "cron_rounds") and conn.execute("SELECT 1 FROM cron_rounds WHERE task_id=? AND state IN ('pending','active')", (task_id,)).fetchone():
        return "同任务 Cron 轮次或维护尚未结束"
    if conn.execute("SELECT 1 FROM executions WHERE task_id=? AND status IN ('pending','running')", (task_id,)).fetchone():
        return "同任务已有本机排队或运行"
    if _table(conn, "execution_settlements") and conn.execute("""SELECT 1 FROM executions e JOIN execution_settlements s ON s.execution_id=e.id
        WHERE e.task_id=? AND s.cleanup_confirmed=0""", (task_id,)).fetchone():
        return "同任务进程或资源收尾尚未确认"
    if _table(conn, "remote_runs") and conn.execute("SELECT 1 FROM remote_runs WHERE task_id=? AND status IN ('queued','preparing','running','stopping','uncertain')", (task_id,)).fetchone():
        return "同任务已有远程活动运行"
    if _table(conn, "maintenance_jobs") and conn.execute("""SELECT 1 FROM maintenance_jobs WHERE task_id=? AND
        (status IN ('pending','generating','ready','testing') OR (status='activated' AND rerun_execution_id IS NOT NULL AND ended_at IS NULL))""", (task_id,)).fetchone():
        return "同任务自动维护或修复重跑尚未结束"
    return ""


def _confirmed(conn, execution_id):
    if not _table(conn, "execution_settlements"):
        return False
    item = conn.execute("SELECT cleanup_confirmed FROM execution_settlements WHERE execution_id=?", (execution_id,)).fetchone()
    return bool(item and item["cleanup_confirmed"])


def _cancel_pending(conn, execution_id, reason):
    now = utc_now()
    cursor = conn.execute("UPDATE executions SET status='cancelled',ended_at=?,error_message=? WHERE id=? AND status='pending' AND started_at IS NULL", (now, reason[:1000], execution_id))
    if cursor.rowcount:
        from ..native_process import confirm_unstarted
        confirm_unstarted(conn, execution_id)
        task = conn.execute("SELECT task_id FROM executions WHERE id=?", (execution_id,)).fetchone()
        conn.execute(f"UPDATE tasks SET last_status={TASK_EXECUTION_STATUS_SQL},updated_at=? WHERE id=?", ("cancelled", now, task["task_id"]))


def _finish(conn, run_id, state, reason):
    run = _run(conn, run_id)
    if run["state"] not in ACTIVE:
        return
    downstream = "cancelled" if state == "cancelled" else "skipped"
    now = utc_now()
    conn.execute("UPDATE workflow_steps SET state=?,ended_at=?,error_summary=? WHERE run_id=? AND state='waiting' AND execution_id IS NULL", (downstream, now, reason[:1000], run_id))
    cursor = conn.execute("UPDATE workflow_runs SET state=?,reason=?,ended_at=? WHERE id=? AND state IN ('waiting','running','stopping')", (state, reason[:1000], now, run_id))
    if cursor.rowcount:
        logger.info("工作流运行 %s 结束：%s%s", run_id, state, f"，{reason}" if reason else "")


def _wait(conn, run_id, reason):
    conn.execute("UPDATE workflow_runs SET reason=? WHERE id=? AND state IN ('waiting','running','stopping')", (reason[:1000], run_id))


def _coordinate(conn, run_id):
    run = _run(conn, run_id)
    if run["state"] not in ACTIVE:
        return []
    steps = [dict(item) for item in conn.execute("SELECT * FROM workflow_steps WHERE run_id=? ORDER BY position", (run_id,))]
    for step in steps:
        if step["state"] == "success":
            # On recovery, saved step state is not a replacement for its actual
            # execution and independently persisted cleanup proof.
            execution = conn.execute("SELECT task_id,status FROM executions WHERE id=?", (step["execution_id"],)).fetchone() if step["execution_id"] is not None else None
            if not execution or execution["task_id"] != step["task_id"] or execution["status"] != "success" or not _confirmed(conn, step["execution_id"]):
                _wait(conn, run_id, "前序成功步骤的执行或收尾关联不完整，等待管理员核对")
                return []
            continue
        if step["execution_id"] is not None:
            execution = conn.execute("SELECT * FROM executions WHERE id=?", (step["execution_id"],)).fetchone()
            if not execution or execution["task_id"] != step["task_id"]:
                _wait(conn, run_id, "步骤执行关联缺失或矛盾，等待管理员核对")
                return []
            execution = dict(execution)
            state = execution["status"]
            if run["stop_requested"]:
                _cancel_pending(conn, execution["id"], run["reason"] or "流程已请求停止")
                state = conn.execute("SELECT status FROM executions WHERE id=?", (execution["id"],)).fetchone()["status"]
            if state in {"pending", "running"}:
                if not run["stop_requested"]:
                    try:
                        assert_execution_allowed(conn, execution["id"])
                    except InterruptedError as exc:
                        return _stop(conn, run_id, str(exc))
                waiting_reason = (execution.get("error_message") or "")[:1000] if state == "pending" else ""
                conn.execute("UPDATE workflow_steps SET state=?,started_at=?,error_summary=? WHERE id=?", ("stopping" if run["stop_requested"] else state, execution.get("started_at"), waiting_reason, step["id"]))
                conn.execute("UPDATE workflow_runs SET state=?,current_step=?,started_at=COALESCE(started_at,?),reason=? WHERE id=?", ("stopping" if run["stop_requested"] else "running", step["position"], execution.get("started_at"), run["reason"] if run["stop_requested"] else waiting_reason, run_id))
                return [execution["id"]] if state == "running" and run["stop_requested"] else []
            if state not in TERMINAL:
                _wait(conn, run_id, "步骤执行状态无效，等待管理员核对")
                return []
            if not _confirmed(conn, execution["id"]):
                stop_reason = run["reason"].split("；等待步骤", 1)[0] if run["stop_requested"] else ""
                _wait(conn, run_id, (stop_reason + "；" if stop_reason else "") + "等待步骤进程树、公共工作区和执行器返回确认")
                return []
            error = (execution.get("error_message") or execution.get("result_message") or "")[:1000]
            conn.execute("UPDATE workflow_steps SET state=?,started_at=?,ended_at=?,error_summary=? WHERE id=?", (state, execution.get("started_at"), execution.get("ended_at") or utc_now(), error, step["id"]))
            conn.execute("UPDATE workflow_runs SET started_at=COALESCE(started_at,?) WHERE id=?", (execution.get("started_at"), run_id))
            if run["stop_requested"] or state == "cancelled":
                _finish(conn, run_id, "cancelled", run["reason"] or error or "当前步骤已取消")
                return []
            if state != "success":
                _finish(conn, run_id, "failed", error or f"步骤“{step['task_name']}”{state}")
                return []
            logger.info("工作流运行 %s 步骤 %s 成功并确认收尾", run_id, step["position"])
            continue
        if step["state"] in {"cancelled", "skipped"}:
            if run["stop_requested"]:
                _finish(conn, run_id, "cancelled", run["reason"] or "流程已请求停止")
            else:
                _finish(conn, run_id, "failed", step["error_summary"] or "步骤状态不完整")
            return []
        if run["stop_requested"]:
            _finish(conn, run_id, "cancelled", run["reason"] or "流程已请求停止")
            return []
        task = conn.execute("SELECT enabled,archived FROM tasks WHERE id=?", (step["task_id"],)).fetchone()
        if not task or task["archived"] or not task["enabled"]:
            _finish(conn, run_id, "cancelled", f"步骤任务“{step['task_name']}”已删除或停用")
            return []
        try:
            if _digest(step["snapshot"].encode()) != step["snapshot_digest"]:
                raise ValueError("流程快照清单哈希不一致")
            snapshot = json.loads(step["snapshot"])
            if not isinstance(snapshot, dict) or snapshot.get("id") != step["task_id"] or snapshot.get("position") != step["position"]:
                raise ValueError("流程快照关联无效")
            _verify_snapshot(snapshot)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            conn.execute("UPDATE workflow_steps SET state='failed',ended_at=?,error_summary=? WHERE id=?", (utc_now(), str(exc)[:1000], step["id"]))
            _finish(conn, run_id, "failed", f"步骤“{step['task_name']}”快照不可用：{exc}")
            return []
        busy = task_busy_reason(conn, step["task_id"])
        if busy:
            conn.execute("UPDATE workflow_runs SET state='waiting',current_step=? WHERE id=? AND stop_requested=0", (step["position"], run_id))
            _wait(conn, run_id, f"步骤“{step['task_name']}”等待：{busy}")
            return []
        now = utc_now()
        execution_id = int(conn.execute("""INSERT INTO executions(task_id,status,trigger_source,requested_by,
            script_path_snapshot,python_path_snapshot,maintenance_snapshot,created_at)
            VALUES(?,'pending','workflow',?,?,?,'',?)""", (step["task_id"], run["requested_by"], snapshot["script_path_snapshot"], snapshot["python_path_snapshot"], now)).lastrowid)
        conn.execute("UPDATE workflow_steps SET execution_id=?,state='pending' WHERE id=? AND execution_id IS NULL", (execution_id, step["id"]))
        conn.execute("UPDATE workflow_runs SET state='running',current_step=?,reason='' WHERE id=?", (step["position"], run_id))
        conn.execute(f"UPDATE tasks SET last_status={TASK_EXECUTION_STATUS_SQL},updated_at=? WHERE id=?", ("pending", now, step["task_id"]))
        logger.info("工作流运行 %s 当前步骤 %s 入队 execution %s", run_id, step["position"], execution_id)
        return []
    _finish(conn, run_id, "cancelled" if run["stop_requested"] else "success", run["reason"] if run["stop_requested"] else "")
    return []


def _stop(conn, run_id, reason):
    run = _run(conn, run_id)
    if run["state"] not in ACTIVE:
        return []
    conn.execute("UPDATE workflow_runs SET stop_requested=1,state='stopping',reason=? WHERE id=? AND state IN ('waiting','running','stopping')", (reason[:1000], run_id))
    conn.execute("UPDATE workflow_steps SET state='cancelled',ended_at=?,error_summary=? WHERE run_id=? AND state='waiting' AND execution_id IS NULL", (utc_now(), reason[:1000], run_id))
    return _coordinate(conn, run_id)


def request_stop(run_id, actor, conn=None):
    with _connection(conn) as opened:
        run = _run(opened, run_id)
        ids = []
        if run["state"] in ACTIVE:
            actor_name = str(actor).strip() if isinstance(actor, str) else "成员"
            if isinstance(actor, int) and _table(opened, "users"):
                user = opened.execute("SELECT display_name FROM users WHERE id=?", (actor,)).fetchone()
                if user and user["display_name"]:
                    actor_name = user["display_name"]
            reason = run["reason"] if run["stop_requested"] else f"由 {actor_name or '成员'} 请求停止流程"
            ids = _stop(opened, run_id, reason)
        result = get_run(opened, run_id)
        result["stop_execution_ids"] = ids
        return result


def reconcile(conn=None, recover=False):
    with _connection(conn) as opened:
        if not has_schema(opened):
            return []
        if recover:
            # Startup only: no retry attempt. Cleanup is checked independently.
            now = utc_now()
            opened.execute("""UPDATE executions SET status='failed',ended_at=?,error_message='主控意外中断，流程不自动重跑此步骤'
                WHERE status='running' AND id IN (SELECT execution_id FROM workflow_steps WHERE execution_id IS NOT NULL)""", (now,))
        ids = []
        for item in opened.execute("SELECT id FROM workflow_runs WHERE state IN ('waiting','running','stopping') ORDER BY id").fetchall():
            ids.extend(_coordinate(opened, item["id"]))
        return list(dict.fromkeys(ids))


def delete_task_history(conn, task_id):
    """Drop ended-step full snapshots, preserving the six-field summary first.

    The caller removes returned managed step directories only after its database
    transaction commits. An unfinished reference refuses deletion.
    """
    if not has_schema(conn):
        return []
    if active_task_reference(conn, task_id):
        raise RuntimeError("任务仍被未结束流程引用，请停止相关流程并确认收尾后删除")
    folders = []
    for item in conn.execute("SELECT id,snapshot,snapshot_storage_dir FROM workflow_steps WHERE task_id=?", (task_id,)).fetchall():
        if item["snapshot"] or item["snapshot_storage_dir"]:
            try:
                folder = _managed_path(item["snapshot_storage_dir"], SNAPSHOT_ROOT, "历史快照目录") if item["snapshot_storage_dir"] else _managed_path(json.loads(item["snapshot"])["script_path_snapshot"], SNAPSHOT_ROOT, "历史快照源码").parent
                if not folder.is_relative_to(SNAPSHOT_ROOT.resolve()) or folder == SNAPSHOT_ROOT.resolve():
                    raise ValueError("目录不在管理范围")
                folders.append(str(folder))
            except (ValueError, TypeError, KeyError) as exc:
                raise RuntimeError("历史流程快照目录无效，已停止删除") from exc
        conn.execute("UPDATE workflow_steps SET snapshot='',snapshot_digest='',snapshot_storage_dir=? WHERE id=?", (str(folder) if item["snapshot"] or item["snapshot_storage_dir"] else "", item["id"]))
    return list(dict.fromkeys(folders))


def prepare_task_deletion(task_id):
    """Persist cancellation of every round referencing the member, first."""
    with transaction() as conn:
        if not has_schema(conn):
            return []
        ids = []
        runs = conn.execute("""SELECT DISTINCT r.id FROM workflow_runs r JOIN workflow_steps s ON s.run_id=r.id
            WHERE s.task_id=? AND r.state IN ('waiting','running','stopping') ORDER BY r.id""", (task_id,)).fetchall()
        for item in runs:
            ids.extend(_stop(conn, item["id"], "成员任务请求删除，正在停止相关流程并确认收尾"))
        return list(dict.fromkeys(ids))


def remove_snapshot_directories(directories):
    """Remove only recorded ended-step storage beneath our snapshot root."""
    root = SNAPSHOT_ROOT.resolve()
    for value in directories:
        path = _managed_path(value, SNAPSHOT_ROOT, "流程快照清理目录")
        # The fixed layout has exactly two components: random round / position.
        if not path.is_relative_to(root) or len(path.relative_to(root).parts) != 2:
            raise RuntimeError("流程快照清理目录不在管理范围")
        with transaction() as conn:
            if not has_schema(conn):
                raise RuntimeError("流程历史表不存在，无法确认清理归属")
            rows = conn.execute("SELECT id,run_id FROM workflow_steps WHERE snapshot_storage_dir=?", (str(path),)).fetchall()
            if not rows:
                raise RuntimeError("流程快照目录没有持久归属，已停止清理")
            for item in rows:
                if _run(conn, item["run_id"])["state"] in ACTIVE:
                    raise RuntimeError("流程快照仍被活动轮次引用")
            if path.is_dir():
                shutil.rmtree(path)
            conn.execute("UPDATE workflow_steps SET snapshot_storage_dir='' WHERE snapshot_storage_dir=? AND snapshot=''", (str(path),))
        # Random parent contains no full history once all its member files are gone.
        try:
            path.parent.rmdir()
        except OSError:
            pass
