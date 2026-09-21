"""Interactive session checks and Windows-owned process Job Objects."""

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import tempfile

# Matches the legacy controller until its in-process desktop executor is retired.
# This value cannot be configured through the production CLI or environment.
_PRODUCT_LOCK_KEY = "SpiderFly.PrimaryScheduler.8E0BF420-47BD-4D9E-AC0B-7BBE55BC1B86"


class SingleInstance:
    def __init__(self, directory: Path, *, key: str | None = None):
        # key is only a test seam; the CLI always uses the fixed physical-host key.
        self.stream, self.mutex, self.machine_file = None, None, None
        digest = hashlib.sha256((key or _PRODUCT_LOCK_KEY).encode("utf-8")).hexdigest().upper()
        try:
            if os.name == "nt":
                self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
                self.kernel.CreateMutexW.restype = wintypes.HANDLE
                self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                self.kernel.CloseHandle.restype = wintypes.BOOL
                ctypes.set_last_error(0)
                self.mutex = self.kernel.CreateMutexW(None, False, f"Global\\SpiderFly.SingleInstance.{digest}")
                error = ctypes.get_last_error()
                if not self.mutex:
                    raise ctypes.WinError(error)
                if error == 183:
                    raise RuntimeError("这台电脑已有 SpiderFly Agent 或旧版本机主控正在运行；本增量请在第二台电脑运行 Agent")
            else:
                import fcntl
                self.machine_file = (Path(tempfile.gettempdir()) / f"spiderfly-{digest}.lock").open("a+b")
                fcntl.flock(self.machine_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            directory.mkdir(parents=True, exist_ok=True)
            self.stream = (directory / "agent.lock").open("a+b")
            if os.fstat(self.stream.fileno()).st_size == 0:
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.close()
            raise RuntimeError("无法独占本机执行资源，可能已有 Agent 或旧版本机主控运行") from error
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        if self.mutex:
            self.kernel.CloseHandle(self.mutex)
            self.mutex = None
        if self.machine_file:
            self.machine_file.close()
            self.machine_file = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def interactive_session() -> bool:
    if os.name != "nt":
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    session_id = wintypes.DWORD()
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)) or not session_id.value:
        return False
    buffer, size = ctypes.c_void_p(), wintypes.DWORD()
    wts.WTSQuerySessionInformationW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
                                               ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
    if not wts.WTSQuerySessionInformationW(None, session_id, 8, ctypes.byref(buffer), ctypes.byref(size)):
        return False
    try:
        if ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int))[0] != 0:  # WTSActive
            return False
    finally:
        wts.WTSFreeMemory(buffer)
    user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user.OpenInputDesktop.restype = wintypes.HANDLE
    user.CloseDesktop.argtypes = [wintypes.HANDLE]
    user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                               wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    desktop = user.OpenInputDesktop(0, False, 1)
    if not desktop:
        return False
    try:
        name, required = ctypes.create_unicode_buffer(256), wintypes.DWORD()
        return bool(user.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name),
                                                   ctypes.byref(required)) and name.value.lower() == "default")
    finally:
        user.CloseDesktop(desktop)


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IOCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]


class ProcessJob:
    """Owned process tree only. Closing the agent kills all remaining job members."""
    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                        ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                          ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self):
        if self.handle and not self.kernel.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def active_count(self) -> int:
        if not self.handle:
            return 0
        result = _Accounting()
        if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(result), ctypes.sizeof(result), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return result.ActiveProcesses

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
