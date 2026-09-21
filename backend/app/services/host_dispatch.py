"""LAN Agent identity, immutable run snapshots and durable per-host leases.

This increment deliberately never submits remote work to the legacy local worker.
All lease/event changes share the controller's SQLite transaction boundary.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException

from .. import database
from ..collection_progress import feed_progress, progress_view
from ..config import DATA_DIR, SERVER_URL

PROTOCOL_VERSION = 1
CURRENT_AGENT_VERSION = "0.3.0"
MINIMUM_AGENT_VERSION = "0.3.0"
HEARTBEAT_TIMEOUT = 15
SIGNATURE_WINDOW = 90
MAX_SCRIPT_BYTES = 2 * 1024 * 1024
MAX_REQUIREMENTS_BYTES = 128 * 1024
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    parts = value.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def require_compatible_agent(protocol_version: int, agent_version: str) -> None:
    if protocol_version != PROTOCOL_VERSION:
        raise HTTPException(
            409,
            f"Agent 协议版本不兼容：主控要求 v{PROTOCOL_VERSION}，"
            "请从主控宿主机页面下载并人工升级 Agent",
        )
    supplied = _version_tuple(agent_version)
    minimum = _version_tuple(MINIMUM_AGENT_VERSION)
    if supplied is None or minimum is None or supplied < minimum:
        raise HTTPException(
            409,
            f"Agent {agent_version or '未知版本'} 已过旧；最低支持 {MINIMUM_AGENT_VERSION}，"
            "请从主控宿主机页面下载并人工升级，主控不会自动更新 Agent",
        )


def init_tables() -> None:
    with database.transaction() as conn:
        statements = (
            """CREATE TABLE IF NOT EXISTS agent_hosts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                public_key TEXT NOT NULL, approval_status TEXT NOT NULL DEFAULT 'pending',
                dispatch_enabled INTEGER NOT NULL DEFAULT 0,
                agent_dispatch_enabled INTEGER NOT NULL DEFAULT 0,
                interactive_session INTEGER NOT NULL DEFAULT 0,
                agent_version TEXT NOT NULL DEFAULT '', last_seen_at TEXT,
                active_run_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS agent_enrollment_codes (
                code_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL,
                consumed_by INTEGER REFERENCES agent_hosts(id),
                created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS agent_nonces (
                host_id INTEGER NOT NULL REFERENCES agent_hosts(id),
                nonce TEXT NOT NULL, expires_at REAL NOT NULL,
                PRIMARY KEY(host_id, nonce)
            )""",
            """CREATE TABLE IF NOT EXISTS remote_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                host_id INTEGER NOT NULL REFERENCES agent_hosts(id),
                task_id INTEGER NOT NULL, task_name TEXT NOT NULL,
                requested_by INTEGER REFERENCES users(id), request_id TEXT NOT NULL UNIQUE,
                trigger_source TEXT NOT NULL DEFAULT 'manual', scheduled_for TEXT,
                code_version_id INTEGER, version_sequence INTEGER,
                script TEXT NOT NULL, requirements TEXT NOT NULL,
                package_hash TEXT NOT NULL, timeout_seconds INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', stop_requested INTEGER NOT NULL DEFAULT 0,
                claimed_at TEXT,
                ack_seq INTEGER NOT NULL DEFAULT 0, exit_code INTEGER,
                error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT,
                collection_progress TEXT NOT NULL DEFAULT '{}',
                notify_on_failure INTEGER NOT NULL DEFAULT 1,
                notification_status TEXT NOT NULL DEFAULT 'pending',
                notification_error TEXT NOT NULL DEFAULT '',
                controller_url TEXT NOT NULL DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS remote_events (
                run_id INTEGER NOT NULL REFERENCES remote_runs(id), seq INTEGER NOT NULL,
                payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(run_id, seq)
            )""",
            """CREATE TABLE IF NOT EXISTS remote_artifacts (
                run_id INTEGER NOT NULL REFERENCES remote_runs(id), name TEXT NOT NULL,
                size INTEGER NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(run_id, name)
            )""",
            """CREATE TABLE IF NOT EXISTS remote_run_notice_reads (
                run_id INTEGER NOT NULL REFERENCES remote_runs(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                read_at TEXT NOT NULL,
                PRIMARY KEY(run_id, user_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_remote_runs_queue ON remote_runs(host_id,status,id)",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_one_lease ON remote_runs(host_id)
                WHERE status IN ('preparing','running','stopping','uncertain')""",
        )
        for statement in statements:
            conn.execute(statement)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(remote_runs)")}
        if "claimed_at" not in columns:
            conn.execute("ALTER TABLE remote_runs ADD COLUMN claimed_at TEXT")
        if "trigger_source" not in columns:
            conn.execute("ALTER TABLE remote_runs ADD COLUMN trigger_source TEXT NOT NULL DEFAULT 'manual'")
        if "scheduled_for" not in columns:
            conn.execute("ALTER TABLE remote_runs ADD COLUMN scheduled_for TEXT")
        if "code_version_id" not in columns:
            conn.execute("ALTER TABLE remote_runs ADD COLUMN code_version_id INTEGER")
        if "version_sequence" not in columns:
            conn.execute("ALTER TABLE remote_runs ADD COLUMN version_sequence INTEGER")
        if "collection_progress" not in columns:
            conn.execute("ALTER TABLE remote_runs ADD COLUMN collection_progress TEXT NOT NULL DEFAULT '{}'")
        if "notify_on_failure" not in columns:
            conn.execute("ALTER TABLE remote_runs ADD COLUMN notify_on_failure INTEGER NOT NULL DEFAULT 1")
            conn.execute(
                """UPDATE remote_runs SET notify_on_failure=COALESCE(
                    (SELECT notify_on_failure FROM tasks WHERE tasks.id=remote_runs.task_id),1
                )"""
            )
        if "notification_status" not in columns:
            conn.execute("ALTER TABLE remote_runs ADD COLUMN notification_status TEXT NOT NULL DEFAULT 'pending'")
            conn.execute(
                """UPDATE remote_runs SET notification_status='historical'
                   WHERE status IN ('succeeded','failed','cancelled','timed_out')"""
            )
        if "notification_error" not in columns:
            conn.execute("ALTER TABLE remote_runs ADD COLUMN notification_error TEXT NOT NULL DEFAULT ''")
        if "controller_url" not in columns:
            conn.execute("ALTER TABLE remote_runs ADD COLUMN controller_url TEXT NOT NULL DEFAULT ''")
        conn.execute(
            """UPDATE remote_runs
               SET notification_status='unknown',
                   notification_error='服务重启，发送结果未确认，不自动重发'
               WHERE notification_status='sending'"""
        )
        from .. import remote_maintenance
        remote_maintenance.init_tables(conn)


def _audit(conn, actor: dict | None, action: str, target: int | None, summary: str) -> None:
    conn.execute(
        """INSERT INTO audit_logs(user_id,username,action,target_type,target_id,summary,created_at)
           VALUES (?,?,?,'agent_host',?,?,?)""",
        (actor["id"] if actor else None, actor["username"] if actor else "agent",
         action, target, summary, database.utc_now()),
    )


def host_row(conn, host_id: int) -> dict:
    row = conn.execute("SELECT * FROM agent_hosts WHERE id=?", (host_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "宿主机不存在")
    return dict(row)


def validate_target_host(conn, host_id: int | None) -> None:
    if host_id is None:
        return
    host = conn.execute(
        "SELECT approval_status FROM agent_hosts WHERE id=?", (host_id,)
    ).fetchone()
    if not host:
        raise HTTPException(400, "计划运行宿主机不存在")
    if host["approval_status"] != "approved":
        raise HTTPException(409, "计划运行宿主机尚未批准或已撤销")


def _online(host: dict) -> bool:
    if not host.get("last_seen_at"):
        return False
    return (datetime.now(timezone.utc) - datetime.fromisoformat(host["last_seen_at"])).total_seconds() < HEARTBEAT_TIMEOUT


def _expire_leases(conn) -> None:
    # An offer that was never acknowledged cannot have started and is safe to
    # requeue/cancel. Once claimed, only a terminal Agent event frees the host.
    for row in conn.execute("SELECT * FROM agent_hosts WHERE active_run_id IS NOT NULL").fetchall():
        host = dict(row)
        if not _online(host):
            run = _run_row(conn, host["active_run_id"])
            if run["claimed_at"] is None and run["status"] in {"preparing", "stopping"}:
                if run["stop_requested"]:
                    conn.execute("UPDATE remote_runs SET status='cancelled',finished_at=? WHERE id=?",
                                 (database.utc_now(), run["id"]))
                else:
                    conn.execute("UPDATE remote_runs SET status='queued' WHERE id=?", (run["id"],))
                conn.execute("UPDATE agent_hosts SET active_run_id=NULL WHERE id=?", (host["id"],))
            else:
                conn.execute(
                    "UPDATE remote_runs SET status='uncertain' WHERE id=? AND status IN ('preparing','running','stopping')",
                    (host["active_run_id"],),
                )


def public_host(conn, host: dict) -> dict:
    item = {key: value for key, value in host.items() if key != "public_key"}
    for key in ("dispatch_enabled", "agent_dispatch_enabled", "interactive_session"):
        item[key] = bool(item[key])
    item["online"] = _online(host)
    approval = host["approval_status"]
    active = conn.execute("SELECT status FROM remote_runs WHERE id=?", (host["active_run_id"],)).fetchone()
    if approval != "approved":
        state = approval
    elif active and active["status"] == "uncertain":
        state = "uncertain"
    elif not item["online"]:
        state = "offline"
    elif active:
        state = "stopping" if active["status"] == "stopping" else "running"
    elif not all(item[key] for key in ("dispatch_enabled", "agent_dispatch_enabled", "interactive_session")):
        state = "non_dispatch"
    else:
        state = "idle"
    item["state"] = state
    return item


def list_hosts() -> list[dict]:
    with database.transaction() as conn:
        _expire_leases(conn)
        return [public_host(conn, dict(row)) for row in conn.execute("SELECT * FROM agent_hosts ORDER BY id DESC")]


def create_code(actor: dict) -> dict:
    code = secrets.token_urlsafe(24)
    expires = time.time() + 20 * 60
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO agent_enrollment_codes(code_hash,expires_at,created_by,created_at) VALUES(?,?,?,?)",
            (hashlib.sha256(code.encode()).hexdigest(), expires, actor["id"], database.utc_now()),
        )
        _audit(conn, actor, "agent_enrollment_code", None, "生成一次性宿主机接入码（20 分钟有效）")
    return {"code": code, "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat()}


def register(payload: dict) -> dict:
    require_compatible_agent(payload["protocol_version"], payload["agent_version"])
    try:
        key = base64.b64decode(payload["public_key"], validate=True)
        Ed25519PublicKey.from_public_bytes(key)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "机器公钥必须是 Ed25519 公钥") from exc
    code_hash = hashlib.sha256(payload["enrollment_code"].encode()).hexdigest()
    with database.transaction() as conn:
        code = conn.execute("SELECT * FROM agent_enrollment_codes WHERE code_hash=?", (code_hash,)).fetchone()
        current = conn.execute("SELECT * FROM agent_hosts WHERE machine_id=?", (payload["machine_id"],)).fetchone()
        if current:
            if (code and code["consumed_by"] == current["id"]
                    and current["public_key"] == payload["public_key"]
                    and current["approval_status"] in {"pending", "approved"}):
                return {"host_id": current["id"], "approval_status": current["approval_status"],
                        "protocol_version": PROTOCOL_VERSION,
                        "minimum_agent_version": MINIMUM_AGENT_VERSION,
                        "current_agent_version": CURRENT_AGENT_VERSION}
            raise HTTPException(409, "该机器身份已存在；重新安装请生成新的机器身份并申请")
        if not code or code["expires_at"] <= time.time() or code["consumed_by"] is not None:
            raise HTTPException(400, "接入码无效、已使用或已过期，请重新生成")
        now = database.utc_now()
        cursor = conn.execute(
            "INSERT INTO agent_hosts(machine_id,name,public_key,agent_version,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (payload["machine_id"], payload["name"], payload["public_key"], payload["agent_version"], now, now),
        )
        host_id = cursor.lastrowid
        conn.execute("UPDATE agent_enrollment_codes SET consumed_by=? WHERE code_hash=?", (host_id, code_hash))
        _audit(conn, None, "agent_register", host_id, "宿主机申请接入，等待管理员批准")
    return {"host_id": host_id, "approval_status": "pending",
            "protocol_version": PROTOCOL_VERSION,
            "minimum_agent_version": MINIMUM_AGENT_VERSION,
            "current_agent_version": CURRENT_AGENT_VERSION}


def authenticate(headers, method: str, path: str, body: bytes) -> int:
    try:
        host_id = int(headers.get("X-SpiderFly-Host", ""))
        timestamp = headers.get("X-SpiderFly-Time", "")
        nonce = headers.get("X-SpiderFly-Nonce", "")
        signature = base64.b64decode(headers.get("X-SpiderFly-Signature", ""), validate=True)
        if abs(time.time() - int(timestamp)) > SIGNATURE_WINDOW or not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", nonce):
            raise ValueError("invalid timestamp or nonce")
        message = "\n".join((timestamp, nonce, method.upper(), path, hashlib.sha256(body).hexdigest())).encode()
        with database.transaction() as conn:
            host = host_row(conn, host_id)
            if host["approval_status"] in {"rejected", "revoked"}:
                raise HTTPException(403, "宿主机接入已被拒绝或撤销")
            Ed25519PublicKey.from_public_bytes(base64.b64decode(host["public_key"])).verify(signature, message)
            conn.execute("DELETE FROM agent_nonces WHERE expires_at < ?", (time.time(),))
            if conn.execute("SELECT 1 FROM agent_nonces WHERE host_id=? AND nonce=?", (host_id, nonce)).fetchone():
                raise HTTPException(409, "机器请求已使用，请用新 nonce 重试")
            conn.execute("INSERT INTO agent_nonces VALUES(?,?,?)", (host_id, nonce, time.time() + SIGNATURE_WINDOW * 2))
        return host_id
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise HTTPException(401, "机器请求签名无效或时间偏差过大") from exc


def change_approval(host_id: int, action: str, actor: dict) -> dict:
    with database.transaction() as conn:
        host = host_row(conn, host_id)
        desired = {"approve": "approved", "reject": "rejected", "revoke": "revoked"}[action]
        if host["approval_status"] == desired:
            return public_host(conn, host)
        if action in {"approve", "reject"} and host["approval_status"] != "pending":
            raise HTTPException(409, "只能批准或拒绝待批准的宿主机")
        if action == "revoke" and host["active_run_id"] is not None:
            raise HTTPException(409, "请先停止任务并等待执行端确认，再撤销宿主机")
        conn.execute("UPDATE agent_hosts SET approval_status=?,dispatch_enabled=0,updated_at=? WHERE id=?", (desired, database.utc_now(), host_id))
        if desired in {"rejected", "revoked"}:
            conn.execute("UPDATE remote_runs SET status='cancelled',finished_at=?,error='宿主机已撤销' WHERE host_id=? AND status='queued'", (database.utc_now(), host_id))
        if desired == "revoked":
            conn.execute(
                """UPDATE tasks SET enabled=0,next_run_at=NULL,updated_at=?
                   WHERE target_host_id=? AND enabled=1""",
                (database.utc_now(), host_id),
            )
        _audit(conn, actor, "agent_" + action, host_id, {"approve": "批准宿主机", "reject": "拒绝宿主机", "revoke": "撤销宿主机"}[action])
        return public_host(conn, host_row(conn, host_id))


def set_mode(host_id: int, enabled: bool, actor: dict) -> dict:
    with database.transaction() as conn:
        host = host_row(conn, host_id)
        if host["approval_status"] != "approved":
            raise HTTPException(409, "只有已批准宿主机可以设置调度模式")
        if host["active_run_id"] is not None and not enabled:
            raise HTTPException(409, "请先停止任务并等待确认，再切换非调度模式")
        conn.execute("UPDATE agent_hosts SET dispatch_enabled=?,updated_at=? WHERE id=?", (int(enabled), database.utc_now(), host_id))
        _audit(conn, actor, "agent_mode", host_id, "开启调度模式" if enabled else "切换非调度模式")
        return public_host(conn, host_row(conn, host_id))


def _run_row(conn, run_id: int, host_id: int | None = None) -> dict:
    row = conn.execute("SELECT * FROM remote_runs WHERE id=?", (run_id,)).fetchone()
    if not row or (host_id is not None and row["host_id"] != host_id):
        raise HTTPException(404, "远程运行不存在")
    return dict(row)


def public_run(conn, run: dict) -> dict:
    item = {key: value for key, value in run.items() if key not in {"script", "requirements", "request_id"}}
    item["collection_progress"] = progress_view(item.get("collection_progress"))
    host = host_row(conn, run["host_id"])
    item["host_name"] = host["name"]
    item["stop_requested"] = bool(run["stop_requested"])
    state = public_host(conn, host)["state"]
    item["waiting_reason"] = ""
    if run["status"] == "queued":
        item["waiting_reason"] = {
            "offline": "目标宿主机离线", "non_dispatch": "等待主控与本机开启调度，并保持用户已登录",
            "running": "目标宿主机忙碌", "stopping": "等待目标宿主机停止", "uncertain": "等待确认旧任务状态",
        }.get(state, "等待目标宿主机领取")
    from .. import remote_maintenance
    item["maintenance"] = remote_maintenance.public_for_run(conn, run["id"])
    return item


def create_run_in_transaction(conn, host_id: int, task_id: int, request_id: str, actor: dict,
                              *, expected_version_id: int | None = None,
                              controller_url: str = "", trigger_source: str = "manual",
                              scheduled_for: str | None = None) -> dict:
    existing = conn.execute("SELECT * FROM remote_runs WHERE request_id=?", (request_id,)).fetchone()
    if existing:
        if existing["host_id"] != host_id or existing["task_id"] != task_id or existing["requested_by"] != actor["id"]:
            raise HTTPException(409, "该请求 ID 已用于其他任务或宿主机")
        return public_run(conn, dict(existing))
    host = host_row(conn, host_id)
    if host["approval_status"] != "approved":
        raise HTTPException(409, "目标宿主机尚未批准或已撤销")
    task = conn.execute(
            """SELECT t.*,a.script_path AS app_script_path,a.requirements_text,a.template_path
               FROM tasks t LEFT JOIN rpa_apps a ON a.id=t.app_id
               WHERE t.id=? AND t.archived=0""", (task_id,),
    ).fetchone()
    if not task:
        raise HTTPException(404, "任务不存在")
    if task["template_path"]:
        raise HTTPException(409, "首个远程增量暂不支持输入模板，请选择不依赖模板的普通 Python 任务")
    try:
        from .. import maintenance
        snapshot = json.loads(maintenance.capture_snapshot(conn, task_id))
        version = conn.execute(
                "SELECT * FROM task_code_versions WHERE id=? AND task_id=?",
                (snapshot["code_version_id"], task_id),
        ).fetchone()
        if not version:
            raise ValueError("任务版本不存在")
        if expected_version_id is not None and version["id"] != expected_version_id:
            raise HTTPException(409, "当前版本与确认的候选不一致，未创建远程重跑")
        policy = conn.execute("SELECT runtime FROM maintenance_policies WHERE task_id=?", (task_id,)).fetchone()
        if policy and policy["runtime"] not in {"native", ""}:
            raise HTTPException(409, "该任务依赖专用运行环境，目前远程分发仅支持普通 Python 任务")
        script, requirements = version["source"], version["requirements"]
        if len(script.encode("utf-8")) > MAX_SCRIPT_BYTES:
            raise HTTPException(413, "脚本超过 2 MiB 限制")
    except HTTPException:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(409, f"任务版本无法冻结：{exc}") from exc
    if len(requirements.encode()) > MAX_REQUIREMENTS_BYTES:
        raise HTTPException(413, "依赖清单超过 128 KiB 限制")
    digest = hashlib.sha256(script.encode() + b"\0" + requirements.encode()).hexdigest()
    cursor = conn.execute(
            """INSERT INTO remote_runs(host_id,task_id,task_name,requested_by,request_id,
               trigger_source,scheduled_for,
               code_version_id,version_sequence,script,requirements,package_hash,timeout_seconds,
               notify_on_failure,controller_url,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (host_id, task_id, task["name"], actor["id"], request_id,
             trigger_source, scheduled_for,
             version["id"], version["sequence"], script, requirements, digest,
             task["timeout_seconds"], int(bool(task["notify_on_failure"])), controller_url[:2000],
             database.utc_now()),
    )
    _audit(conn, actor, "remote_run_create", host_id, f"分发任务 {task_id}，远程运行 {cursor.lastrowid}")
    return public_run(conn, _run_row(conn, cursor.lastrowid))


def create_run(host_id: int, task_id: int, request_id: str, actor: dict,
               controller_url: str = "", trigger_source: str = "manual",
               scheduled_for: str | None = None) -> dict:
    with database.transaction() as conn:
        return create_run_in_transaction(
            conn, host_id, task_id, request_id, actor, controller_url=controller_url,
            trigger_source=trigger_source, scheduled_for=scheduled_for,
        )


def dispatch_scheduled_task(task_id: int, source: str = "schedule") -> int:
    """Freeze one due occurrence for its configured host, or use the legacy local queue."""
    with database.transaction() as conn:
        task = conn.execute(
            """SELECT t.target_host_id,t.last_triggered_at,t.created_by,u.username
               FROM tasks t LEFT JOIN users u ON u.id=t.created_by
               WHERE t.id=? AND t.archived=0 AND t.enabled=1""",
            (task_id,),
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在或已停用")
        if task["target_host_id"] is not None:
            scheduled_for = task["last_triggered_at"] or database.utc_now()
            request_id = f"schedule:{task_id}:{scheduled_for}"
            actor = {"id": task["created_by"], "username": task["username"] or "scheduler"}
            previous = conn.execute(
                """SELECT controller_url FROM remote_runs
                   WHERE host_id=? AND controller_url!='' ORDER BY id DESC LIMIT 1""",
                (task["target_host_id"],),
            ).fetchone()
            controller_url = SERVER_URL or (previous["controller_url"] if previous else "")
            run = create_run_in_transaction(
                conn,
                int(task["target_host_id"]),
                task_id,
                request_id,
                actor,
                controller_url=controller_url,
                trigger_source=source,
                scheduled_for=scheduled_for,
            )
            return int(run["id"])
    from .execution_queue import _enqueue_task_sync
    return _enqueue_task_sync(task_id, source)


def heartbeat(host_id: int, payload: dict) -> dict:
    with database.transaction() as conn:
        _expire_leases(conn)
        host = host_row(conn, host_id)
        if host["approval_status"] in {"rejected", "revoked"}:
            raise HTTPException(403, "宿主机接入已被拒绝或撤销")
        require_compatible_agent(payload["protocol_version"], payload["agent_version"])
        conn.execute(
            """UPDATE agent_hosts SET last_seen_at=?,updated_at=?,agent_version=?,
               agent_dispatch_enabled=?,interactive_session=? WHERE id=?""",
            (database.utc_now(), database.utc_now(), payload["agent_version"],
             int(payload["dispatch_enabled"]), int(payload["interactive_session"]), host_id),
        )
        active_id = host["active_run_id"]
        reported = payload.get("active_run_id")
        if reported is not None and reported != active_id:
            # A terminal event response may have been lost. Allow the Agent to replay it.
            reported_run = _run_row(conn, reported, host_id)
            if active_id is None and reported_run["status"] == "queued" and reported_run["claimed_at"] is None:
                # The offer timed out before its claim reached the controller.
                # Reattach the same durable local claim; execution still needs claim_run authorization.
                active_id = reported
                conn.execute("UPDATE remote_runs SET status='preparing' WHERE id=?", (reported,))
                conn.execute("UPDATE agent_hosts SET active_run_id=? WHERE id=?", (reported, host_id))
            elif reported_run["status"] not in TERMINAL:
                raise HTTPException(409, "Agent 运行与主控租约不一致，请保留本机状态后核对")
        if active_id is not None:
            run = _run_row(conn, active_id)
            if (payload["recovery_required"]
                    or (reported is None and run["status"] in {"running", "stopping"})
                    or (reported is None and run["status"] == "preparing" and run["claimed_at"] is not None)):
                conn.execute("UPDATE remote_runs SET status='uncertain' WHERE id=?", (active_id,))
            elif reported == active_id and run["status"] == "uncertain" and not payload["recovery_required"]:
                conn.execute("UPDATE remote_runs SET status=? WHERE id=?", ("stopping" if run["stop_requested"] else "running", active_id))
        host = host_row(conn, host_id)
        if (active_id is None and reported is None and not payload["recovery_required"]
                and host["approval_status"] == "approved"
                and host["dispatch_enabled"] and host["agent_dispatch_enabled"] and host["interactive_session"]):
            row = conn.execute("SELECT id FROM remote_runs WHERE host_id=? AND status='queued' ORDER BY id LIMIT 1", (host_id,)).fetchone()
            if row:
                active_id = row["id"]
                conn.execute("UPDATE remote_runs SET status='preparing' WHERE id=?", (active_id,))
                conn.execute("UPDATE agent_hosts SET active_run_id=? WHERE id=?", (active_id, host_id))
        run = _run_row(conn, active_id) if active_id is not None else None
        offer = None
        if run and run["status"] == "preparing" and run["started_at"] is None:
            offer = {key: run[key] for key in ("id", "task_name", "timeout_seconds", "package_hash")}
        return {"host": public_host(conn, host_row(conn, host_id)), "run": offer,
                "stop_requested": bool(run and run["stop_requested"]),
                "protocol_version": PROTOCOL_VERSION,
                "minimum_agent_version": MINIMUM_AGENT_VERSION,
                "current_agent_version": CURRENT_AGENT_VERSION}


def claim_run(host_id: int, run_id: int) -> dict:
    """Authorize execution only after the Agent has durably stored the offer."""
    with database.transaction() as conn:
        host = host_row(conn, host_id)
        run = _run_row(conn, run_id, host_id)
        if host["approval_status"] != "approved" or host["active_run_id"] != run_id:
            raise HTTPException(409, "任务租约已失效，不能确认领取")
        if run["stop_requested"] or run["status"] == "stopping":
            return {"stop_requested": True}
        if run["status"] not in {"preparing", "uncertain"} or run["started_at"] is not None:
            raise HTTPException(409, "任务已经开始或状态不允许确认领取")
        if run["claimed_at"] is None:
            conn.execute("UPDATE remote_runs SET claimed_at=?,status='preparing' WHERE id=?",
                         (database.utc_now(), run_id))
        elif run["status"] == "uncertain":
            # An idempotent retry from the same durable local claim can safely
            # recover an authorization response lost before execution began.
            conn.execute("UPDATE remote_runs SET status='preparing' WHERE id=?", (run_id,))
        return {key: run[key] for key in ("id", "task_name", "timeout_seconds", "package_hash")}


def package(host_id: int, run_id: int) -> dict:
    with database.transaction() as conn:
        host = host_row(conn, host_id)
        run = _run_row(conn, run_id, host_id)
        if host["approval_status"] != "approved" or host["active_run_id"] != run_id:
            raise HTTPException(409, "任务未领取或已停止，不能下载任务包")
        if run["stop_requested"]:
            # The admin may stop after the offer but before this download. Give
            # the owning Agent an explicit cancellation, without executable code.
            return {"stop_requested": True}
        if run["claimed_at"] is None or run["status"] not in {"preparing", "running"}:
            raise HTTPException(409, "运行状态待确认或已结束，不能下载任务包")
        return {key: run[key] for key in ("script", "requirements", "package_hash")}


def accept_events(host_id: int, run_id: int, events: list[dict]) -> dict:
    with database.transaction() as conn:
        host = host_row(conn, host_id)
        if host["approval_status"] != "approved":
            raise HTTPException(403, "宿主机尚未批准")
        run = _run_row(conn, run_id, host_id)
        for event in events:
            encoded = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            previous = conn.execute("SELECT payload_json FROM remote_events WHERE run_id=? AND seq=?", (run_id, event["seq"])).fetchone()
            if previous:
                if previous["payload_json"] != encoded:
                    raise HTTPException(409, "相同事件序号的内容冲突")
                continue
            if run["status"] in TERMINAL or host["active_run_id"] != run_id:
                raise HTTPException(409, "运行已结束或未领取，不接受新事件")
            if event["seq"] != run["ack_seq"] + 1:
                raise HTTPException(409, f"事件序号不连续，下一序号应为 {run['ack_seq'] + 1}")
            kind = event["kind"]
            if kind == "started":
                if run["started_at"] is not None:
                    raise HTTPException(409, "运行已经开始，不能再次开始")
                run["started_at"] = database.utc_now()
                run["status"] = "stopping" if run["stop_requested"] else "running"
            elif kind == "uncertain":
                run["status"] = "uncertain"
                run["error"] = event["text"][:2000]
            elif kind == "finished":
                if event["status"] not in TERMINAL:
                    raise HTTPException(400, "结束事件必须包含有效终态")
                if event["status"] == "succeeded" and event.get("exit_code") != 0:
                    raise HTTPException(400, "成功事件必须包含退出码 0")
                run.update(status=event["status"], finished_at=database.utc_now(), exit_code=event["exit_code"], error=event["text"][:2000])
                run["notification_status"] = (
                    "pending" if event["status"] in {"failed", "timed_out"} and run["notify_on_failure"]
                    else "disabled"
                )
                conn.execute("UPDATE agent_hosts SET active_run_id=NULL WHERE id=? AND active_run_id=?", (host_id, run_id))
                _audit(conn, None, "remote_run_finished", host_id, f"远程运行 {run_id}：{run['status']}")
            if kind == "stdout":
                run["collection_progress"] = feed_progress(
                    run.get("collection_progress", "{}"), event["text"], database.utc_now(),
                )
            run["ack_seq"] = event["seq"]
            conn.execute("INSERT INTO remote_events VALUES(?,?,?,?)", (run_id, event["seq"], encoded, database.utc_now()))
            conn.execute(
                """UPDATE remote_runs SET status=?,started_at=?,finished_at=?,exit_code=?,error=?,ack_seq=?,
                   collection_progress=?,notification_status=? WHERE id=?""",
                (run["status"], run["started_at"], run["finished_at"], run["exit_code"], run["error"],
                 run["ack_seq"], run.get("collection_progress", "{}"), run["notification_status"], run_id),
            )
            if kind == "finished":
                from .. import remote_maintenance
                remote_maintenance.enqueue_failure(conn, run)
        return {"ack_seq": run["ack_seq"], "stop_requested": bool(run["stop_requested"])}


def _duration_ms(run: dict) -> int:
    try:
        started = datetime.fromisoformat(run["started_at"] or run["created_at"])
        finished = datetime.fromisoformat(run["finished_at"])
        return max(0, int((finished - started).total_seconds() * 1000))
    except (TypeError, ValueError):
        return 0


def notify_next() -> bool:
    """Attempt one durable remote failure notification without automatic retries."""
    with database.transaction() as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_runs'"
        ).fetchone():
            return False
        row = conn.execute(
            """SELECT r.*,h.name AS host_name
               FROM remote_runs r JOIN agent_hosts h ON h.id=r.host_id
               WHERE r.status IN ('failed','timed_out') AND r.notification_status='pending'
               ORDER BY r.id LIMIT 1"""
        ).fetchone()
        if not row:
            return False
        run = dict(row)
        conn.execute(
            "UPDATE remote_runs SET notification_status='sending',notification_error='' WHERE id=?",
            (run["id"],),
        )

    from .. import ai_settings
    from ..feishu import FeishuNotifier

    try:
        notifier = FeishuNotifier()
        if not notifier.configured:
            status, note = "skipped", "未配置飞书群 Webhook 或应用收件人"
        else:
            version = f"v{run['version_sequence']}" if run.get("version_sequence") else "版本未知"
            notifier.send_final_result(
                task_name=f"{run['task_name']}｜{run['host_name']}｜远程 #{run['id']}｜{version}",
                status="failed",
                duration_ms=_duration_ms(run),
                error_summary=ai_settings.redact(run["error"] or "远程任务异常结束")[:1000],
                result_code="REMOTE_TIMEOUT" if run["status"] == "timed_out" else "REMOTE_FAILED",
                manual_action_url=(
                    f"{run['controller_url'].rstrip('/')}/?remote_run={run['id']}"
                    if run.get("controller_url") else ""
                ),
            )
            status, note = "sent", "飞书通知已发送"
    except Exception as error:
        status, note = "failed", ai_settings.redact(str(error))[:1000]
    database.execute(
        "UPDATE remote_runs SET notification_status=?,notification_error=? WHERE id=?",
        (status, note, run["id"]),
    )
    return True


def list_runs() -> list[dict]:
    with database.transaction() as conn:
        _expire_leases(conn)
        return [public_run(conn, dict(row)) for row in conn.execute("SELECT * FROM remote_runs ORDER BY id DESC LIMIT 100")]


def run_detail(run_id: int) -> dict:
    with database.transaction() as conn:
        _expire_leases(conn)
        item = public_run(conn, _run_row(conn, run_id))
        item["events"] = [dict(json.loads(row["payload_json"]), created_at=row["created_at"]) for row in conn.execute(
            "SELECT * FROM (SELECT * FROM remote_events WHERE run_id=? ORDER BY seq DESC LIMIT 500) ORDER BY seq", (run_id,))]
        item["events_truncated"] = item["ack_seq"] > len(item["events"])
        item["artifacts"] = [dict(row, download_url=f"/api/remote-runs/{run_id}/artifacts/{quote(row['name'], safe='')}") for row in conn.execute(
            "SELECT name,size,sha256 FROM remote_artifacts WHERE run_id=? ORDER BY name", (run_id,))]
        return item


def list_failure_notices(user: dict) -> dict:
    """Remote failures are the durable notice source; reads are per administrator."""
    from .. import ai_settings

    with database.transaction() as conn:
        rows = conn.execute(
            """SELECT r.id,r.task_id,r.task_name,r.status,r.error,r.finished_at,
                      h.name AS host_name,n.read_at
               FROM remote_runs r JOIN agent_hosts h ON h.id=r.host_id
               LEFT JOIN remote_run_notice_reads n ON n.run_id=r.id AND n.user_id=?
               WHERE r.status IN ('failed','timed_out')
               ORDER BY r.id DESC LIMIT 50""",
            (user["id"],),
        ).fetchall()
        unread = conn.execute(
            """SELECT COUNT(*) AS count FROM remote_runs r
               LEFT JOIN remote_run_notice_reads n ON n.run_id=r.id AND n.user_id=?
               WHERE r.status IN ('failed','timed_out') AND n.read_at IS NULL""",
            (user["id"],),
        ).fetchone()["count"]
        items = []
        for row in rows:
            item = dict(row)
            item["error"] = ai_settings.redact(item["error"])[:1000]
            items.append(item)
        return {"items": items, "unread_count": unread}


def read_failure_notice(run_id: int, user: dict) -> dict:
    with database.transaction() as conn:
        run = _run_row(conn, run_id)
        if run["status"] not in {"failed", "timed_out"}:
            raise HTTPException(404, "远程失败提醒不存在")
        conn.execute(
            """INSERT INTO remote_run_notice_reads(run_id,user_id,read_at) VALUES(?,?,?)
               ON CONFLICT(run_id,user_id) DO NOTHING""",
            (run_id, user["id"], database.utc_now()),
        )
    return {"read": True}


def stop_run(run_id: int, actor: dict) -> dict:
    with database.transaction() as conn:
        run = _run_row(conn, run_id)
        if run["status"] not in TERMINAL:
            if run["status"] == "queued":
                conn.execute("UPDATE remote_runs SET status='cancelled',stop_requested=1,finished_at=? WHERE id=?", (database.utc_now(), run_id))
            else:
                status = "uncertain" if run["status"] == "uncertain" else "stopping"
                conn.execute("UPDATE remote_runs SET status=?,stop_requested=1 WHERE id=?", (status, run_id))
            _audit(conn, actor, "remote_run_stop", run["host_id"], f"请求停止远程运行 {run_id}")
        return public_run(conn, _run_row(conn, run_id))


def artifact_path(run_id: int, name: str) -> Path:
    if (not name or len(name) > 160 or name in {".", ".."} or name.endswith((".", " "))
            or any(char in name for char in '/\\:<>"|?*') or any(ord(char) < 32 for char in name)
            or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
        raise HTTPException(400, "产物文件名不安全")
    base = (DATA_DIR / "remote_runs" / str(run_id) / "artifacts").resolve()
    path = base / name
    if path.is_symlink() or not path.resolve().is_relative_to(base):
        raise HTTPException(400, "产物路径越界")
    return path


def save_artifact(host_id: int, run_id: int, name: str, content: bytes) -> dict:
    if len(content) > MAX_ARTIFACT_BYTES:
        raise HTTPException(413, "产物超过 16 MiB 限制")
    path = artifact_path(run_id, name)
    digest = hashlib.sha256(content).hexdigest()
    with database.transaction() as conn:
        run = _run_row(conn, run_id, host_id)
        host = host_row(conn, host_id)
        if host["approval_status"] != "approved":
            raise HTTPException(403, "宿主机尚未批准")
        previous = next((row for row in conn.execute("SELECT * FROM remote_artifacts WHERE run_id=?", (run_id,))
                         if row["name"].casefold() == name.casefold()), None)
        if previous:
            if previous["sha256"] != digest or previous["name"] != name:
                raise HTTPException(409, "同名产物内容冲突")
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
                return {"name": name, "size": len(content), "sha256": digest}
        if run["status"] in TERMINAL or host["active_run_id"] != run_id:
            raise HTTPException(409, "只能上传当前运行的产物")
        totals = conn.execute("SELECT COUNT(*) AS count,COALESCE(SUM(size),0) AS total FROM remote_artifacts WHERE run_id=?", (run_id,)).fetchone()
        if not previous and (totals["count"] >= 100 or totals["total"] + len(content) > 64 * 1024 * 1024):
            raise HTTPException(413, "本次运行产物超过 100 个或合计 64 MiB")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(".upload-" + secrets.token_hex(12))
        try:
            temporary.write_bytes(content)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        if not previous:
            conn.execute("INSERT INTO remote_artifacts VALUES(?,?,?,?,?)", (run_id, name, len(content), digest, database.utc_now()))
    return {"name": name, "size": len(content), "sha256": digest}


def download_artifact(run_id: int, name: str) -> Path:
    path = artifact_path(run_id, name)
    record = database.fetch_one("SELECT size,sha256 FROM remote_artifacts WHERE run_id=? AND name=?", (run_id, name))
    if not record or not path.is_file():
        raise HTTPException(404, "产物不存在")
    if path.stat().st_size != record["size"] or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise HTTPException(409, "产物完整性校验失败")
    return path
