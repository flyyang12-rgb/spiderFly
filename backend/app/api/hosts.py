"""Administrator host management and signed machine-only HTTP protocol."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..security import admin_user
from ..services import host_dispatch as service

router = APIRouter()
AGENT_ROOT = Path(__file__).resolve().parents[3] / "agent"


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Registration(Payload):
    enrollment_code: str = Field(min_length=10, max_length=200)
    machine_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    public_key: str = Field(min_length=40, max_length=100)
    agent_version: str = Field(default="0.1.0", max_length=40)
    protocol_version: int = 1


class Heartbeat(Payload):
    protocol_version: int
    agent_version: str = Field(max_length=40)
    dispatch_enabled: bool
    interactive_session: bool
    active_run_id: int | None = Field(default=None, gt=0)
    recovery_required: bool = False


class Mode(Payload):
    dispatch_enabled: bool


class Dispatch(Payload):
    task_id: int = Field(gt=0)
    request_id: str = Field(min_length=16, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class Event(Payload):
    seq: int = Field(gt=0)
    kind: Literal["started", "stdout", "stderr", "finished", "uncertain"]
    text: str = Field(default="", max_length=16000)
    status: Literal["succeeded", "failed", "cancelled", "timed_out"] | None = None
    exit_code: int | None = None


class Events(Payload):
    events: list[Event] = Field(min_length=1, max_length=100)


def write_admin(request: Request, user: dict = Depends(admin_user)) -> dict:
    origin = request.headers.get("origin") or request.headers.get("referer")
    expected = urlsplit(str(request.base_url))
    if not origin:
        raise HTTPException(403, "管理操作缺少同源信息，请从主控网页执行")
    supplied = urlsplit(origin)
    if supplied.scheme != expected.scheme or supplied.netloc.lower() != expected.netloc.lower():
        raise HTTPException(403, "请从主控网页执行此操作")
    # Requiring JSON for non-body actions also prevents form-based CSRF.
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        raise HTTPException(415, "管理操作需要 application/json 请求")
    return user


async def limited_body(request: Request, limit: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(413, "请求内容超过大小限制")
    return bytes(body)


async def signed(request: Request, limit: int = 2 * 1024 * 1024) -> tuple[int, bytes]:
    body = await limited_body(request, limit)
    return service.authenticate(request.headers, request.method, request.url.path, body), body


def parse(model, body: bytes) -> dict:
    try:
        return model.model_validate_json(body).model_dump()
    except ValidationError as exc:
        # Never echo submitted credentials or payloads in validation errors.
        raise HTTPException(422, "请求字段格式不符合 Agent 协议") from exc


@router.get("/api/hosts")
def hosts(user: dict = Depends(admin_user)):
    return service.list_hosts()


@router.get("/api/hosts/agent-download")
def agent_download(user: dict = Depends(admin_user)):
    # Explicit source allowlist: never include local identity, environments or journals.
    sources = [AGENT_ROOT / name for name in (
        "README.md", "requirements.txt", "start.ps1", "manage.ps1", "setup.ps1",
        "install-python.ps1", "安装并接入Agent.bat",
    )]
    sources += [AGENT_ROOT / "spiderfly_agent" / (name + ".py") for name in (
        "__init__", "__main__", "api", "client", "desktop", "identity", "journal", "windows", "worker",
    )]
    if any(not path.is_file() or path.is_symlink()
                              or not path.resolve().is_relative_to(AGENT_ROOT.resolve()) for path in sources):
        raise HTTPException(503, "Agent 安装文件不完整，请更新主控源码")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sources:
            bundle.write(path, "SpiderFlyAgent/" + path.relative_to(AGENT_ROOT).as_posix())
    return Response(archive.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="SpiderFlyAgent-{service.CURRENT_AGENT_VERSION}.zip"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


@router.post("/api/hosts/enrollment-codes", status_code=201)
def enrollment_code(request: Request, user: dict = Depends(write_admin)):
    return {**service.create_code(user), "server_url": str(request.base_url).rstrip("/")}


@router.post("/api/hosts/{host_id}/approve")
def approve(host_id: int, user: dict = Depends(write_admin)):
    return service.change_approval(host_id, "approve", user)


@router.post("/api/hosts/{host_id}/reject")
def reject(host_id: int, user: dict = Depends(write_admin)):
    return service.change_approval(host_id, "reject", user)


@router.post("/api/hosts/{host_id}/revoke")
def revoke(host_id: int, user: dict = Depends(write_admin)):
    return service.change_approval(host_id, "revoke", user)


@router.patch("/api/hosts/{host_id}/mode")
def mode(host_id: int, payload: Mode, user: dict = Depends(write_admin)):
    return service.set_mode(host_id, payload.dispatch_enabled, user)


@router.post("/api/hosts/{host_id}/runs", status_code=201)
def dispatch(request: Request, host_id: int, payload: Dispatch, user: dict = Depends(write_admin)):
    return service.create_run(
        host_id, payload.task_id, payload.request_id, user,
        controller_url=str(request.base_url).rstrip("/"),
    )


@router.get("/api/remote-runs")
def runs(user: dict = Depends(admin_user)):
    return service.list_runs()


@router.get("/api/remote-run-notices")
def failure_notices(user: dict = Depends(admin_user)):
    return service.list_failure_notices(user)


@router.post("/api/remote-run-notices/{run_id}/read")
def read_failure_notice(run_id: int, user: dict = Depends(write_admin)):
    return service.read_failure_notice(run_id, user)


@router.get("/api/remote-runs/{run_id}")
def details(run_id: int, user: dict = Depends(admin_user)):
    return service.run_detail(run_id)


@router.post("/api/remote-runs/{run_id}/stop")
def stop(run_id: int, user: dict = Depends(write_admin)):
    return service.stop_run(run_id, user)


@router.get("/api/remote-runs/{run_id}/artifacts/{name}")
def artifact_download(run_id: int, name: str, user: dict = Depends(admin_user)):
    return FileResponse(service.download_artifact(run_id, name), filename=name, media_type="application/octet-stream",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.post("/api/agent/register", status_code=201)
async def register(request: Request):
    return service.register(parse(Registration, await limited_body(request, 4096)))


@router.post("/api/agent/heartbeat")
async def heartbeat(request: Request):
    host_id, body = await signed(request, 4096)
    return service.heartbeat(host_id, parse(Heartbeat, body))


@router.get("/api/agent/runs/{run_id}/package")
async def package(run_id: int, request: Request):
    host_id, _ = await signed(request, 0)
    return service.package(host_id, run_id)


@router.post("/api/agent/runs/{run_id}/claim")
async def claim(run_id: int, request: Request):
    host_id, _ = await signed(request, 0)
    return service.claim_run(host_id, run_id)


@router.post("/api/agent/runs/{run_id}/events")
async def events(run_id: int, request: Request):
    host_id, body = await signed(request)
    return service.accept_events(host_id, run_id, parse(Events, body)["events"])


@router.post("/api/agent/runs/{run_id}/artifacts/{name}")
async def artifact_upload(run_id: int, name: str, request: Request):
    host_id, body = await signed(request, service.MAX_ARTIFACT_BYTES)
    return service.save_artifact(host_id, run_id, name, body)
