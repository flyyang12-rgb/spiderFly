from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_env_file(path: Path | None = None) -> None:
    """Load simple KEY=VALUE settings without overriding process variables."""
    env_path = path or (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


load_env_file()


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _resolved_path(name: str, default: Path) -> Path:
    configured = Path(os.getenv(name, str(default))).expanduser()
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


DATA_DIR = _resolved_path("SPIDERFLY_DATA_DIR", PROJECT_ROOT / "data")
RPA_APPS_DIR = _resolved_path("SPIDERFLY_APPS_DIR", DATA_DIR / "apps")
RPA_ENVS_DIR = _resolved_path("SPIDERFLY_ENVS_DIR", DATA_DIR / "envs")
EXECUTIONS_DIR = (DATA_DIR / "executions").resolve()
WORK_DIR = _resolved_path("SPIDERFLY_WORK_DIR", PROJECT_ROOT / "共享工作区")
DEFAULT_TASK_TIMEOUT_SECONDS = _bounded_int(
    "SPIDERFLY_TASK_TIMEOUT_SECONDS", 600, 60, 86400
)
HOST_CHECK_INTERVAL_SECONDS = _bounded_int(
    "SPIDERFLY_HOST_CHECK_INTERVAL_SECONDS", 2, 1, 30
)
MANAGED_BROWSER_PORT = _bounded_int(
    "SPIDERFLY_BROWSER_PORT", 9123, 1024, 65535
)
BASE_PYTHON = os.getenv("SPIDERFLY_BASE_PYTHON", "").strip() or getattr(
    sys, "_base_executable", sys.executable
)
VENV_TIMEOUT_SECONDS = _bounded_int("SPIDERFLY_VENV_TIMEOUT_SECONDS", 180, 30, 3600)
PIP_TIMEOUT_SECONDS = _bounded_int("SPIDERFLY_PIP_TIMEOUT_SECONDS", 1800, 60, 86400)
ENV_VERIFY_TIMEOUT_SECONDS = _bounded_int(
    "SPIDERFLY_ENV_VERIFY_TIMEOUT_SECONDS", 60, 10, 600
)
SESSION_HOURS = _bounded_int("SPIDERFLY_SESSION_HOURS", 12, 1, 24 * 30)
COOKIE_SECURE = os.getenv("SPIDERFLY_COOKIE_SECURE", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SESSION_COOKIE_NAME = "spiderfly_session"
