"""Persistent controller polling while an independent worker owns execution."""

import hashlib
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import quote

import psutil
import requests

from . import PROTOCOL_VERSION, __version__
from .api import Api, ApiError
from .windows import interactive_session
from .worker import Worker, safe_artifact


def recover(journal):
    """Do not rerun interrupted IDs; only known safe cleanup can release occupancy."""
    active = journal.active()
    if not active or active["state"] in {"uploading", "terminal", "uncertain"}:
        return
    run_id = active["id"]
    if active["state"] in {"claimed", "preparing"}:
        journal.complete(run_id, "failed", "Agent 重启：上次执行中断，本次运行不会自动重启")
        return
    if active["job_owned"] and os.name == "nt" and active["pid"] and active["birth"]:
        try:
            same_process = abs(psutil.Process(active["pid"]).create_time() - active["birth"]) < 0.01
        except psutil.NoSuchProcess:
            same_process = False
        except psutil.AccessDenied:
            same_process = True
        if not same_process:
            # The prior Agent was the sole handle owner of a kill-on-close Job Object.
            journal.complete(run_id, "failed", "Agent 重启：原 Windows 进程组已结束，本次运行不会重启")
            return
    journal.uncertain(run_id, "Agent 重启后无法证明旧进程组已结束，保留占用；请检查本机运行状态")


class Client:
    def __init__(self, api, journal, root: Path, *, dispatch=False, session_check=interactive_session,
                 on_status=None, shutdown_file: Path | None = None):
        self.api, self.journal, self.root = api, journal, root.resolve()
        self.dispatch, self.session_check = dispatch, session_check
        self.worker = None
        self.shutdown = threading.Event()
        self.last_status = None
        self.on_status = on_status
        self.status_lock = threading.RLock()
        self.pending_dispatch_off = False
        self.shutdown_file = shutdown_file
        self.progress_buffer = ""
        self.status = {
            "connection": "connecting", "host_state": "unknown", "interactive": False,
            "dispatch": bool(dispatch), "run_id": None, "task_name": "", "run_state": "",
            "progress": "", "started_at": None, "error": "",
        }

    def snapshot(self):
        with self.status_lock:
            return dict(self.status)

    def publish(self, **changes):
        with self.status_lock:
            self.status.update(changes)
            snapshot = dict(self.status)
        if self.on_status:
            try:
                self.on_status(snapshot)
            except Exception:
                pass

    def set_dispatch(self, enabled: bool) -> bool:
        active = self.journal.active()
        if not enabled and active:
            return False
        self.dispatch = bool(enabled)
        self.pending_dispatch_off = False
        self.publish(dispatch=self.dispatch)
        return True

    def request_stop(self, *, emergency=False) -> bool:
        if not self.worker or not self.worker.is_alive():
            return False
        if emergency:
            self.pending_dispatch_off = True
        self.worker.stop_event.set()
        self.publish(run_state="正在紧急接管" if emergency else "正在停止")
        return True

    def worker_event(self, kind: str, text: str):
        if kind == "started":
            self.publish(run_state="运行中", progress="")
        elif kind == "stdout":
            self.progress_buffer += text
            lines = self.progress_buffer.split("\n")
            self.progress_buffer = lines.pop()
            display = ""
            for line in lines:
                if line.startswith("SPIDERFLY_PROGRESS "):
                    try:
                        event = json.loads(line.removeprefix("SPIDERFLY_PROGRESS "))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    parts = [str(event.get("stage") or "处理中")]
                    if event.get("page") is not None:
                        parts.append(f"第 {event['page']} 页")
                    if event.get("collected") is not None:
                        total = f"/{event['total']}" if event.get("total") is not None else ""
                        parts.append(f"已采集 {event['collected']}{total}")
                    display = " · ".join(parts)
                elif line.strip():
                    display = line.strip()
            if display:
                self.publish(progress=display[:120])

    def flush(self):
        active = self.journal.active()
        if not active and self.status["run_id"] is not None:
            self.publish(run_id=None, task_name="", run_state="", progress="", started_at=None)
        if not active:
            return
        run_id = active["id"]
        # Ship each batch separately; the loop returns to heartbeat between large backlogs.
        events = self.journal.pending(run_id)
        if events:
            response = self.api.request("POST", f"/api/agent/runs/{run_id}/events", {"events": events})
            self.journal.acknowledge(run_id, response["ack_seq"])
            if response.get("stop_requested") and self.worker:
                self.worker.stop_event.set()
        active = self.journal.active()
        if not active or active["state"] != "uploading":
            return
        # One upload per loop keeps heartbeat and stop handling responsive.
        pending = self.journal.pending_artifacts(run_id)
        if pending:
            artifact = pending[0]
            path = Path(artifact["path"])
            try:
                data = safe_artifact(path, self.root / "runs" / str(run_id) / "artifacts")
                if hashlib.sha256(data).hexdigest() != artifact["sha256"]:
                    raise ValueError("回传前产物内容发生变化")
            except (OSError, ValueError) as error:
                self.journal.artifact_skipped(run_id, artifact["name"], f"产物 {artifact['name']} 无法回传: {error}")
            else:
                try:
                    self.api.request("POST", f"/api/agent/runs/{run_id}/artifacts/{quote(artifact['name'], safe='')}", raw=data)
                except ApiError as error:
                    if error.status not in {400, 404, 409, 413, 422}:
                        raise
                    self.journal.artifact_skipped(run_id, artifact["name"], f"产物 {artifact['name']} 被主控拒绝: {error}")
                else:
                    self.journal.artifact_uploaded(run_id, artifact["name"])
        self.journal.terminal_after_upload(run_id)

    def tick(self):
        # Even a failing upload must not prevent heartbeat, stop, or revocation handling.
        self.heartbeat()
        self.flush()

    def heartbeat(self):
        active = self.journal.active()
        if self.worker and not self.worker.is_alive():
            if active and active["state"] not in {"uploading", "terminal", "uncertain"}:
                self.journal.uncertain(active["id"], "执行线程意外结束，尚未记录终态，保留占用")
                active = self.journal.active()
            self.worker = None
            if self.pending_dispatch_off:
                self.dispatch = False
                self.pending_dispatch_off = False
            self.publish(dispatch=self.dispatch, run_state="正在回传结果")
        interactive = self.session_check()
        response = self.api.request("POST", "/api/agent/heartbeat", {
            "protocol_version": PROTOCOL_VERSION, "agent_version": __version__,
            "dispatch_enabled": self.dispatch, "interactive_session": interactive,
            "active_run_id": active["id"] if active else None,
            "recovery_required": bool(active and active["recovery_required"]),
        })
        host = response["host"]
        state = host.get("state", host.get("approval_status", "unknown"))
        self.publish(connection="connected", host_state=state, interactive=interactive,
                     dispatch=self.dispatch, error="")
        if state != self.last_status:
            print(f"宿主机 #{self.api.host_id}: {state}", flush=True)
            self.last_status = state
        if self.worker and self.worker.is_alive():
            if response.get("stop_requested") or not interactive:
                self.worker.stop_event.set()
            return
        self.worker = None
        run = response.get("run")
        if run and active:
            if run["id"] != active["id"]:
                raise RuntimeError("主控下发的运行与本机占用不一致，拒绝启动")
        if run and self.dispatch and interactive and host.get("approval_status") == "approved":
            if not active:
                self.journal.claim(run)
                active = self.journal.active()
            if active and active["state"] == "claimed":
                # Persist locally before asking the controller to authorize execution.
                # A lost offer can then be safely reattached without duplicate execution.
                authorized = self.api.request("POST", f"/api/agent/runs/{run['id']}/claim")
                if authorized.get("stop_requested"):
                    self.journal.complete(run["id"], "cancelled", "任务在开始前已停止")
                    self.journal.terminal_after_upload(run["id"])
                    return
                worker_api = Api(self.api.server, self.api.key, self.api.host_id)
                self.publish(run_id=authorized["id"], task_name=authorized.get("task_name", ""),
                             run_state="正在准备", progress="", started_at=time.monotonic())
                self.worker = Worker(authorized, self.root, worker_api, self.journal,
                                     observer=self.worker_event)
                self.worker.start()

    def run(self):
        recover(self.journal)
        failures = 0
        try:
            while not self.shutdown.is_set():
                if self.shutdown_file and self.shutdown_file.exists():
                    print("收到本机停止请求，正在安全结束 Agent。", flush=True)
                    self.shutdown.set()
                    break
                try:
                    self.tick()
                    failures = 0
                except ApiError as error:
                    if error.status in {401, 403} and self.worker:
                        self.worker.stop_event.set()
                    if failures == 0:
                        print(str(error), flush=True)
                    failures += 1
                    self.publish(connection="error", error=str(error))
                except (requests.RequestException, OSError, ValueError, RuntimeError) as error:
                    if failures == 0:
                        print(f"连接或状态校验失败，保留执行记录并重试: {type(error).__name__}: {error}", flush=True)
                    failures += 1
                    self.publish(connection="error", error=f"{type(error).__name__}: {error}")
                self.shutdown.wait(min(10, 2 ** min(failures, 4)) if failures else 2)
        finally:
            if self.worker:
                self.worker.stop_event.set()
                self.worker.join()
            self.publish(connection="stopped", run_state="")
            # Best-effort delivery; any unsent events remain durable for next start.
            try:
                self.flush()
            except (requests.RequestException, ApiError, OSError, ValueError, RuntimeError):
                pass
            if self.shutdown_file:
                self.shutdown_file.unlink(missing_ok=True)
