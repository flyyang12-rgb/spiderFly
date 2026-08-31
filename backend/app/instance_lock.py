from __future__ import annotations

import ctypes
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Callable


ERROR_ALREADY_EXISTS = 183
_PRODUCT_LOCK_KEY = "SpiderFly.PrimaryScheduler.8E0BF420-47BD-4D9E-AC0B-7BBE55BC1B86"


class AlreadyRunningError(RuntimeError):
    """Raised before database recovery when another SpiderFly owns the scheduler."""


class InstanceLock:
    def __init__(self, release: Callable[[], None]) -> None:
        self._release = release

    def close(self) -> None:
        release, self._release = self._release, lambda: None
        release()


def _lock_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest().upper()


def _windows_lock(key: str) -> InstanceLock:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    create_mutex.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_bool

    mutex_name = f"Global\\SpiderFly.SingleInstance.{_lock_digest(key)}"
    ctypes.set_last_error(0)
    handle = create_mutex(None, False, mutex_name)
    last_error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(last_error)
    if last_error == ERROR_ALREADY_EXISTS:
        close_handle(handle)
        raise AlreadyRunningError(
            "SpiderFly 已经在这台电脑上运行，请直接打开现有网页，不要重复启动。"
        )

    def release() -> None:
        if not close_handle(handle):
            error = ctypes.get_last_error()
            if error:
                raise ctypes.WinError(error)

    return InstanceLock(release)


def _posix_lock(key: str) -> InstanceLock:
    import fcntl

    lock_path = Path(tempfile.gettempdir()) / f"spiderfly-{_lock_digest(key)}.lock"
    lock_file = lock_path.open("a+b")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise AlreadyRunningError(
            "SpiderFly 已经在这台电脑上运行，请直接打开现有网页，不要重复启动。"
        ) from exc
    except BaseException:
        lock_file.close()
        raise

    def release() -> None:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()

    return InstanceLock(release)


def acquire_instance_lock(*, key: str | None = None) -> InstanceLock:
    """Acquire the machine-wide SpiderFly scheduler lock.

    ``key`` exists for isolated tests. Production intentionally uses one fixed
    product key so path aliases and alternate ports cannot start two schedulers.
    """

    resolved_key = key or _PRODUCT_LOCK_KEY
    return _windows_lock(resolved_key) if os.name == "nt" else _posix_lock(resolved_key)
