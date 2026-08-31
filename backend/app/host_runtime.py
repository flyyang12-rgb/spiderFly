from __future__ import annotations

import ctypes
import os
import shutil
import socket
import stat
import uuid
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from .config import (
    DATA_DIR,
    EXECUTIONS_DIR,
    MANAGED_BROWSER_PORT,
    PROJECT_ROOT,
    RPA_APPS_DIR,
    RPA_ENVS_DIR,
)


PathLike = str | os.PathLike[str]
ProcessProvider = Callable[[], Iterable["ProcessInfo"]]
PortAvailableProvider = Callable[[int], bool]

EXCEL_PROCESS_NAMES = frozenset({"excel.exe"})


class HostRuntimeError(RuntimeError):
    """Base error for host preparation and cleanup failures."""


class UnsafeWorkDirectoryError(HostRuntimeError):
    """Raised when a configured work directory could erase protected data."""


class WorkDirectoryCleanupError(HostRuntimeError):
    """Raised when the public work directory cannot be made empty and writable."""


class TemplateCopyError(HostRuntimeError):
    """Raised when an optional managed template cannot be staged safely."""


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str


@dataclass(frozen=True)
class HostBusyStatus:
    busy: bool
    code: str
    message: str
    excel_pids: tuple[int, ...] = ()
    browser_port: int | None = None


def _process_basename(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _windows_processes() -> tuple[ProcessInfo, ...]:
    """Read process ids and image names without adding a psutil dependency."""
    if os.name != "nt":
        return ()

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_next = kernel32.Process32NextW
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    process_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
    process_first.restype = wintypes.BOOL
    process_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
    process_next.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    invalid_handle = wintypes.HANDLE(-1).value
    if snapshot in (None, invalid_handle):
        raise OSError(ctypes.get_last_error(), "无法读取 Windows 进程列表")

    results: list[ProcessInfo] = []
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not process_first(snapshot, ctypes.byref(entry)):
            error = ctypes.get_last_error()
            if error == 18:  # ERROR_NO_MORE_FILES
                return ()
            raise OSError(error, "无法读取第一个 Windows 进程")
        while True:
            results.append(ProcessInfo(int(entry.th32ProcessID), str(entry.szExeFile)))
            if not process_next(snapshot, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error == 18:  # ERROR_NO_MORE_FILES
                    break
                raise OSError(error, "读取 Windows 进程列表时发生错误")
    finally:
        close_handle(snapshot)
    return tuple(results)


def _local_port_available(port: int) -> bool:
    """Return whether SpiderFly can bind its loopback-only browser port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", int(port)))
        except OSError:
            return False
    return True


def check_host_busy(
    *,
    platform_name: str | None = None,
    process_provider: ProcessProvider | None = None,
    port_available_provider: PortAvailableProvider | None = None,
    managed_browser_port: int = MANAGED_BROWSER_PORT,
) -> HostBusyStatus:
    """Inspect shared Excel and SpiderFly's reserved browser port.

    Excel is busy whenever EXCEL.EXE exists. Personal Chrome and Edge windows
    do not block the queue; managed browser scripts exclusively use the
    configured loopback port. ShadowBot remains outside this lightweight check.
    """
    effective_platform = os.name if platform_name is None else platform_name
    if effective_platform != "nt":
        return HostBusyStatus(False, "idle", "非 Windows 宿主机，无需检查 Excel 和专用浏览器端口")

    process_provider = process_provider or _windows_processes
    port_available_provider = port_available_provider or _local_port_available
    try:
        processes = tuple(process_provider())
        browser_port_available = bool(port_available_provider(managed_browser_port))
    except Exception as exc:
        return HostBusyStatus(
            True,
            "inspection_failed",
            f"无法检查宿主机上的 Excel 和专用浏览器端口：{exc}",
        )

    excel_pids: set[int] = set()
    for process in processes:
        try:
            pid = int(process.pid)
            name = _process_basename(process.name)
        except (AttributeError, TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        if name in EXCEL_PROCESS_NAMES:
            excel_pids.add(pid)

    sorted_excel = tuple(sorted(excel_pids))
    if not sorted_excel and browser_port_available:
        return HostBusyStatus(False, "idle", "宿主机空闲")

    reasons: list[str] = []
    if sorted_excel:
        reasons.append("Excel 正在运行")
    if not browser_port_available:
        reasons.append(
            f"SpiderFly 专用浏览器端口 {managed_browser_port} 正在使用，可能是上一次浏览器没有退出"
        )
    return HostBusyStatus(
        True,
        "desktop_resource_busy",
        "，".join(reasons) + "，请等待关闭后再运行任务",
        sorted_excel,
        managed_browser_port if not browser_port_available else None,
    )


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    return bool(checker and checker())


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_work_directory(
    work_dir: PathLike,
    *,
    project_root: PathLike = PROJECT_ROOT,
    data_dir: PathLike = DATA_DIR,
    apps_dir: PathLike = RPA_APPS_DIR,
    envs_dir: PathLike = RPA_ENVS_DIR,
    executions_dir: PathLike = EXECUTIONS_DIR,
    user_home: PathLike | None = None,
) -> Path:
    """Resolve one deliberately configured public directory before deletion."""
    configured = Path(work_dir).expanduser()
    if configured.exists() and (configured.is_symlink() or _is_junction(configured)):
        raise UnsafeWorkDirectoryError("公共工作目录本身不能是符号链接或目录联接")

    resolved = configured.resolve(strict=False)
    if not str(resolved).strip() or not resolved.anchor:
        raise UnsafeWorkDirectoryError("公共工作目录不是有效的绝对路径")
    drive_root = Path(resolved.anchor).resolve(strict=False)
    if resolved == drive_root:
        raise UnsafeWorkDirectoryError("公共工作目录不能是磁盘根目录")
    if resolved.exists() and not resolved.is_dir():
        raise UnsafeWorkDirectoryError("公共工作目录路径已经被普通文件占用")

    home = Path(user_home).expanduser() if user_home is not None else Path.home()
    protected = {
        "用户主目录": home.resolve(strict=False),
        "SpiderFly 项目根目录": Path(project_root).expanduser().resolve(strict=False),
        "SpiderFly 数据目录": Path(data_dir).expanduser().resolve(strict=False),
        "Python 程序目录": Path(apps_dir).expanduser().resolve(strict=False),
        "Python 环境目录": Path(envs_dir).expanduser().resolve(strict=False),
        "执行资料目录": Path(executions_dir).expanduser().resolve(strict=False),
    }
    for label, protected_path in protected.items():
        if resolved == protected_path:
            raise UnsafeWorkDirectoryError(f"公共工作目录不能使用{label}本身")
        if _is_relative_to(protected_path, resolved):
            raise UnsafeWorkDirectoryError(
                f"公共工作目录不能包含{label}，否则清理时可能删除系统数据"
            )

    # Managed app, environment and execution subtrees must never double as the
    # disposable public folder. A dedicated data/work child remains allowed.
    for label, managed_root in (
        ("Python 程序目录", protected["Python 程序目录"]),
        ("Python 环境目录", protected["Python 环境目录"]),
        ("执行资料目录", protected["执行资料目录"]),
    ):
        if _is_relative_to(resolved, managed_root):
            raise UnsafeWorkDirectoryError(f"公共工作目录不能位于{label}内")
    return resolved


def _make_writable_then_retry(function: Callable[..., object], path: str, _info: object) -> None:
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    function(path)


def _remove_entry(path: Path) -> None:
    if _is_junction(path):
        os.rmdir(path)
        return
    if path.is_symlink():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path, onerror=_make_writable_then_retry)
        return
    try:
        path.unlink()
    except PermissionError:
        path.chmod(stat.S_IWRITE | stat.S_IREAD)
        path.unlink()


def clear_work_directory(
    work_dir: PathLike,
    *,
    project_root: PathLike = PROJECT_ROOT,
    data_dir: PathLike = DATA_DIR,
    apps_dir: PathLike = RPA_APPS_DIR,
    envs_dir: PathLike = RPA_ENVS_DIR,
    executions_dir: PathLike = EXECUTIONS_DIR,
    user_home: PathLike | None = None,
) -> Path:
    """Empty only the validated public work directory and verify writability."""
    resolved = validate_work_directory(
        work_dir,
        project_root=project_root,
        data_dir=data_dir,
        apps_dir=apps_dir,
        envs_dir=envs_dir,
        executions_dir=executions_dir,
        user_home=user_home,
    )
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        for entry in tuple(resolved.iterdir()):
            _remove_entry(entry)
        if any(resolved.iterdir()):
            raise OSError("清理后目录仍包含文件")
        probe = resolved / f".spiderfly-write-test-{uuid.uuid4().hex}"
        probe.write_bytes(b"")
        probe.unlink()
    except UnsafeWorkDirectoryError:
        raise
    except OSError as exc:
        raise WorkDirectoryCleanupError(f"公共工作目录无法清空或写入：{exc}") from exc
    return resolved


def _validate_template_source(template_path: PathLike, work_dir: Path) -> Path:
    configured = Path(template_path).expanduser()
    if configured.is_symlink() or _is_junction(configured):
        raise TemplateCopyError("Excel 模板不能是符号链接或目录联接")
    try:
        source = configured.resolve(strict=True)
    except OSError as exc:
        raise TemplateCopyError(f"Excel 模板不存在或无法读取：{exc}") from exc
    if not source.is_file():
        raise TemplateCopyError("Excel 模板必须是普通文件")
    if source == work_dir or _is_relative_to(source, work_dir):
        raise TemplateCopyError("Excel 模板原件不能放在会被清空的公共工作目录中")
    return source


def _safe_template_name(template_name: str) -> str:
    name = template_name.strip()
    if not name or name in {".", ".."} or Path(name).name != name:
        raise TemplateCopyError("Excel 模板目标文件名无效")
    if "/" in name or "\\" in name:
        raise TemplateCopyError("Excel 模板目标文件名不能包含目录")
    return name


def prepare_work_directory(
    work_dir: PathLike,
    *,
    template_path: PathLike | None = None,
    template_name: str | None = None,
    project_root: PathLike = PROJECT_ROOT,
    data_dir: PathLike = DATA_DIR,
    apps_dir: PathLike = RPA_APPS_DIR,
    envs_dir: PathLike = RPA_ENVS_DIR,
    executions_dir: PathLike = EXECUTIONS_DIR,
    user_home: PathLike | None = None,
) -> tuple[Path, Path | None]:
    """Clear the public folder and optionally copy one protected template into it."""
    validated = validate_work_directory(
        work_dir,
        project_root=project_root,
        data_dir=data_dir,
        apps_dir=apps_dir,
        envs_dir=envs_dir,
        executions_dir=executions_dir,
        user_home=user_home,
    )
    source = _validate_template_source(template_path, validated) if template_path else None
    target_name = _safe_template_name(template_name or source.name) if source else None
    cleaned = clear_work_directory(
        validated,
        project_root=project_root,
        data_dir=data_dir,
        apps_dir=apps_dir,
        envs_dir=envs_dir,
        executions_dir=executions_dir,
        user_home=user_home,
    )
    if source is None or target_name is None:
        return cleaned, None

    destination = cleaned / target_name
    temporary = cleaned / f".{target_name}.{uuid.uuid4().hex}.tmp"
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise TemplateCopyError(f"Excel 模板复制失败：{exc}") from exc
    return cleaned, destination


def cleanup_after_run(
    work_dir: PathLike,
    *,
    project_root: PathLike = PROJECT_ROOT,
    data_dir: PathLike = DATA_DIR,
    apps_dir: PathLike = RPA_APPS_DIR,
    envs_dir: PathLike = RPA_ENVS_DIR,
    executions_dir: PathLike = EXECUTIONS_DIR,
    user_home: PathLike | None = None,
) -> Path:
    """Clear only the public directory after a run; never terminate global apps."""
    return clear_work_directory(
        work_dir,
        project_root=project_root,
        data_dir=data_dir,
        apps_dir=apps_dir,
        envs_dir=envs_dir,
        executions_dir=executions_dir,
        user_home=user_home,
    )
