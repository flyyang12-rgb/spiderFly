"""Owned native process trees and persistent cleanup receipts for new Cron rounds.

The launcher runs with ``-I -S`` and waits for a private stdin gate. User code
cannot start before assignment to a kill-on-close Job and a durable PID identity.
This module deliberately has no dependency on the separate Agent package.
"""
from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes
import os
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid


def ensure_schema(conn) -> None:
    # No cascading FK: a deletion must never discard an outstanding cleanup fact.
    conn.execute("""CREATE TABLE IF NOT EXISTS execution_settlements (
        execution_id INTEGER PRIMARY KEY,
        state TEXT NOT NULL DEFAULT 'prepared',
        job_name TEXT NOT NULL UNIQUE,
        pid INTEGER,
        process_created_at TEXT NOT NULL DEFAULT '',
        processes_gone INTEGER NOT NULL DEFAULT 0,
        workspace_cleanup_confirmed INTEGER NOT NULL DEFAULT 0,
        runner_returned INTEGER NOT NULL DEFAULT 0,
        external_receipt TEXT NOT NULL DEFAULT '',
        external_token TEXT NOT NULL DEFAULT '',
        cleanup_confirmed INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    columns = {row[1] for row in conn.execute('PRAGMA table_info(execution_settlements)')}
    if 'runner_returned' not in columns:
        conn.execute('ALTER TABLE execution_settlements ADD COLUMN runner_returned INTEGER NOT NULL DEFAULT 0')
    for name in ('external_receipt', 'external_token'):
        if name not in columns:
            conn.execute('ALTER TABLE execution_settlements ADD COLUMN ' + name + " TEXT NOT NULL DEFAULT ''")


def cron_owned(conn, execution_id: int) -> bool:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cron_rounds'").fetchone():
        return False
    return conn.execute(
        'SELECT 1 FROM cron_rounds WHERE execution_id=? OR rerun_execution_id=? LIMIT 1',
        (execution_id, execution_id),
    ).fetchone() is not None


def is_cron_execution(execution_id: int) -> bool:
    from .database import transaction
    with transaction() as conn:
        return cron_owned(conn, execution_id)


def workflow_owned(conn, execution_id: int) -> bool:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_steps'").fetchone():
        return False
    return conn.execute(
        'SELECT 1 FROM workflow_steps WHERE execution_id=? LIMIT 1', (execution_id,)
    ).fetchone() is not None


def coordinated_owned(conn, execution_id: int) -> bool:
    """Only executions durably associated with new Cron or workflow rounds opt in."""
    return cron_owned(conn, execution_id) or workflow_owned(conn, execution_id)


def is_workflow_execution(execution_id: int) -> bool:
    from .database import transaction
    with transaction() as conn:
        return workflow_owned(conn, execution_id)


def is_coordinated_execution(execution_id: int) -> bool:
    from .database import transaction
    with transaction() as conn:
        return coordinated_owned(conn, execution_id)


def cleanup_confirmed(conn, execution_id: int) -> bool:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_settlements'").fetchone():
        return False
    row = conn.execute('SELECT cleanup_confirmed FROM execution_settlements WHERE execution_id=?', (execution_id,)).fetchone()
    return bool(row and row['cleanup_confirmed'])


def confirm_unstarted(conn, execution_id: int) -> bool:
    """Confirm a never-claimed coordinated cancellation in its cancellation transaction."""
    if not coordinated_owned(conn, execution_id):
        return False
    row = conn.execute('SELECT status,started_at FROM executions WHERE id=?', (execution_id,)).fetchone()
    if not row or row['status'] != 'cancelled' or row['started_at'] is not None:
        raise ValueError('已接管的执行不能按未启动确认收尾')
    if conn.execute('SELECT 1 FROM execution_settlements WHERE execution_id=?', (execution_id,)).fetchone():
        raise ValueError('执行已有收尾身份，不能按未启动确认')
    from .database import utc_now
    now = utc_now()
    conn.execute('''INSERT INTO execution_settlements
        (execution_id,state,job_name,processes_gone,workspace_cleanup_confirmed,
         runner_returned,cleanup_confirmed,note,created_at,updated_at)
        VALUES(?,'confirmed',?,1,1,1,1,'never_claimed',?,?)''',
        (execution_id, 'Local\\SpiderFly.Native.' + uuid.uuid4().hex, now, now))
    return True


def begin_settlement(execution_id: int) -> str:
    from .database import transaction
    with transaction() as conn:
        return _begin_settlement_conn(conn, execution_id)


def _begin_settlement_conn(conn, execution_id: int) -> str:
    from .database import utc_now
    job_name = 'Local\\SpiderFly.Native.' + uuid.uuid4().hex
    now = utc_now()
    conn.execute('''INSERT INTO execution_settlements
        (execution_id,job_name,created_at,updated_at) VALUES(?,?,?,?)''',
        (execution_id, job_name, now, now))
    return job_name


def _update(execution_id: int, **values) -> None:
    from .database import transaction
    with transaction() as conn:
        _update_conn(conn, execution_id, **values)


def _update_conn(conn, execution_id: int, **values) -> None:
    from .database import utc_now
    row = conn.execute('SELECT * FROM execution_settlements WHERE execution_id=?', (execution_id,)).fetchone()
    if not row:
        raise RuntimeError('执行收尾身份不存在')
    if row['cleanup_confirmed']:
        return
    process_gone = values.get('processes_gone', row['processes_gone'])
    workspace_clean = values.get('workspace_cleanup_confirmed', row['workspace_cleanup_confirmed'])
    returned = values.get('runner_returned', row['runner_returned'])
    if process_gone and workspace_clean and returned:
        values.update(cleanup_confirmed=1, state='confirmed', note='')
    values['updated_at'] = utc_now()
    columns = ','.join(name + '=?' for name in values)
    conn.execute('UPDATE execution_settlements SET ' + columns + ' WHERE execution_id=?', (*values.values(), execution_id))


def mark_processes_gone(execution_id: int) -> None:
    _update(execution_id, processes_gone=1, state='awaiting_cleanup')


def mark_workspace_cleanup(execution_id: int, success: bool, note: str = '') -> None:
    _update(execution_id, workspace_cleanup_confirmed=int(success), note=note[:1000])


def mark_uncertain(execution_id: int, reason: str) -> None:
    _update(execution_id, state='uncertain', note=reason[:1000])


def mark_runner_returned(execution_id: int) -> None:
    _update(execution_id, runner_returned=1)


def processes_gone(execution_id: int) -> bool:
    from .database import transaction
    with transaction() as conn:
        row = conn.execute('SELECT processes_gone FROM execution_settlements WHERE execution_id=?', (execution_id,)).fetchone()
    return bool(row and row['processes_gone'])


def start_external_settlement(execution_id: int, receipt_path: Path, token: str) -> None:
    if not token or len(token) > 128:
        raise ValueError('外部执行收尾 token 无效')
    from .database import transaction
    with transaction() as conn:
        row = conn.execute('SELECT * FROM execution_settlements WHERE execution_id=?', (execution_id,)).fetchone()
        if row is None:
            _begin_settlement_conn(conn, execution_id)
        elif row['state'] != 'prepared' or row['pid'] is not None or row['processes_gone'] or row['external_receipt']:
            raise RuntimeError('执行已有运行身份，拒绝重新绑定外部执行')
        _update_conn(conn, execution_id, state='external_started',
                     external_receipt=str(Path(receipt_path).resolve()), external_token=token)


def _external_receipt_valid(row) -> bool:
    path = Path(row['external_receipt'])
    if not row['external_token'] or not path.is_file() or path.stat().st_size > 4096:
        return False
    receipt = json.loads(path.read_text(encoding='utf-8'))
    return isinstance(receipt, dict) and receipt.get('token') == row['external_token'] and receipt.get('complete') is True


def verify_external_receipt(execution_id: int) -> bool:
    from .database import transaction
    with transaction() as conn:
        row = conn.execute('SELECT * FROM execution_settlements WHERE execution_id=?', (execution_id,)).fetchone()
        row = dict(row) if row else None
    try:
        valid = bool(row and row['external_receipt'] and _external_receipt_valid(row))
    except (ValueError, OSError):
        valid = False
    if valid:
        mark_processes_gone(execution_id)
    elif row:
        mark_uncertain(execution_id, '外部执行收尾回执缺失或无效')
    return valid


class _BasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ('ReadOperationCount', 'WriteOperationCount',
        'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimits), ('IoInfo', _IOCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [('TotalUserTime', ctypes.c_int64), ('TotalKernelTime', ctypes.c_int64),
                ('ThisPeriodTotalUserTime', ctypes.c_int64), ('ThisPeriodTotalKernelTime', ctypes.c_int64),
                ('TotalPageFaultCount', wintypes.DWORD), ('TotalProcesses', wintypes.DWORD),
                ('ActiveProcesses', wintypes.DWORD), ('TotalTerminatedProcesses', wintypes.DWORD)]


def _kernel():
    if os.name != 'nt':
        raise RuntimeError('新增 Cron 和工作流的原生执行收尾仅支持 Windows')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                       ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _creation_time(kernel, handle) -> str:
    created, exited, user, system = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user)):
        raise ctypes.WinError(ctypes.get_last_error())
    return str((created.dwHighDateTime << 32) | created.dwLowDateTime)


def _active_count(kernel, handle) -> int:
    result = _Accounting()
    if not kernel.QueryInformationJobObject(handle, 1, ctypes.byref(result), ctypes.sizeof(result), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return result.ActiveProcesses


class OwnedJob:
    def __init__(self, execution_id: int, name: str):
        self.execution_id, self.name = execution_id, name
        self.kernel, self.handle = _kernel(), None
        ctypes.set_last_error(0)
        self.handle = self.kernel.CreateJobObjectW(None, name)
        error = ctypes.get_last_error()
        if not self.handle:
            raise ctypes.WinError(error)
        if error == 183:
            self.close()
            raise RuntimeError('执行 Job 身份已存在，拒绝重复启动')
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE; no breakaway.
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign(self, pid: int) -> str:
        handle = self.kernel.OpenProcess(0x0100 | 0x0001 | 0x1000, False, pid)  # SET_QUOTA, TERMINATE, QUERY_LIMITED
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            created = _creation_time(self.kernel, handle)
            _update(self.execution_id, pid=pid, process_created_at=created)
            if not self.kernel.AssignProcessToJobObject(self.handle, handle):
                raise ctypes.WinError(ctypes.get_last_error())
            return created
        finally:
            self.kernel.CloseHandle(handle)

    def active_count(self) -> int:
        if not self.handle:
            raise RuntimeError('Job 已关闭，不能把句柄缺失当作收尾确认')
        return _active_count(self.kernel, self.handle)

    async def finish(self) -> None:
        # A successfully exited parent may have left children. Kill only this Job.
        if self.active_count() and not self.kernel.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + 12
        while self.active_count():
            if time.monotonic() >= deadline:
                raise RuntimeError('本次进程树尚未确认退出')
            await asyncio.sleep(0.05)
        from .database import transaction
        with transaction() as conn:
            row = dict(conn.execute('SELECT * FROM execution_settlements WHERE execution_id=?', (self.execution_id,)).fetchone())
        # Job accounting can reach zero just before the launcher process becomes
        # signaled. Recheck its recorded identity within the same total budget;
        # an inaccessible handle still raises and never counts as confirmation.
        while not _identity_gone(row):
            if time.monotonic() >= deadline:
                raise RuntimeError('启动进程尚未确认退出')
            await asyncio.sleep(0.03)
        mark_processes_gone(self.execution_id)

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


async def launch_owned(execution_id: int, job: OwnedJob, interpreter: str, script: Path, *, cwd: str, env: dict, before_gate=None):
    _update(execution_id, state='launching')
    process = None
    gate_opened = False
    token = uuid.uuid4().hex
    try:
        process = await asyncio.create_subprocess_exec(
            interpreter, '-I', '-S', str(Path(__file__).resolve()), '--launch', token, str(script),
            cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        created = job.assign(process.pid)
        # Do not open the gate until both tree containment and identity are durable.
        _update(execution_id, state='active', pid=process.pid, process_created_at=created)
        if before_gate is not None:
            before_gate()
        gate_opened = True
        process.stdin.write((token + '\n').encode('ascii'))
        await process.stdin.drain()
        process.stdin.close()
        return process
    except BaseException as failure:
        if process:
            if process.stdin:
                process.stdin.close()
            # If assignment failed the launcher has not received its gate.
            if process.returncode is None:
                process.kill()
            try:
                await asyncio.wait_for(process.wait(), 12)
            except (OSError, asyncio.TimeoutError):
                mark_uncertain(execution_id, '启动失败后 launcher 尚未确认退出')
                raise
        try:
            if process is not None and not gate_opened and job.active_count() == 0:
                # The gated launcher was explicitly waited for. It could not
                # have spawned user code, including if assignment failed.
                mark_processes_gone(execution_id)
            elif process is None and not isinstance(failure, asyncio.CancelledError) and job.active_count() == 0:
                mark_processes_gone(execution_id)
            else:
                await job.finish()
        except Exception as exc:
            mark_uncertain(execution_id, str(exc))
        raise


def _identity_gone(row) -> bool:
    if row.get('processes_gone'):
        return True  # A prior receipt, unlike executions.status, is durable evidence.
    if row.get('external_receipt'):
        return _external_receipt_valid(row)
    kernel = _kernel()
    handle = kernel.OpenJobObjectW(0x0004, False, row['job_name'])  # JOB_OBJECT_QUERY
    if handle:
        try:
            if _active_count(kernel, handle):
                return False
        finally:
            kernel.CloseHandle(handle)
    elif ctypes.get_last_error() != 2:  # ERROR_FILE_NOT_FOUND; denied/unknown stays unresolved.
        raise ctypes.WinError(ctypes.get_last_error())
    if row['pid'] is None:
        # A crash between spawn and durable PID capture leaves an unidentified
        # gated launcher. It cannot run user code, but still needs confirmation.
        return row['state'] == 'prepared'
    handle = kernel.OpenProcess(0x1000 | 0x00100000, False, int(row['pid']))  # QUERY_LIMITED, SYNCHRONIZE
    if not handle:
        if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
            return True
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        created = _creation_time(kernel, handle)
        if created != row['process_created_at']:
            return True  # PID reused by an unrelated process; do not stop it.
        result = kernel.WaitForSingleObject(handle, 0)
        if result == 0:
            return True
        if result == 0x102:
            return False
        raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def recover_settlements(*, clean_workspace: bool = False) -> list[int]:
    """Inspect owned identities; workspace cleanup is allowed only at a serial boundary."""
    from .database import transaction
    with transaction() as conn:
        ensure_schema(conn)
        rows = [dict(row) for row in conn.execute('SELECT * FROM execution_settlements WHERE cleanup_confirmed=0')]
    for row in rows:
        try:
            if _identity_gone(row):
                mark_processes_gone(row['execution_id'])
            else:
                mark_uncertain(row['execution_id'], '等待本次进程树退出确认')
        except Exception as exc:
            mark_uncertain(row['execution_id'], '进程归属核对未确认：' + str(exc))
    if clean_workspace:
        from .config import WORK_DIR
        from .host_runtime import cleanup_after_run
        from .runner import _execution_controls
        # Caller must also be before worker start or at its serial boundary; no
        # maintenance/collection trial may run concurrently with this operation.
        with transaction() as conn:
            unresolved = conn.execute('SELECT 1 FROM execution_settlements WHERE cleanup_confirmed=0 AND processes_gone=0').fetchone()
            running = conn.execute("SELECT 1 FROM executions WHERE status='running' LIMIT 1").fetchone()
            targets = [row['execution_id'] for row in conn.execute(
                'SELECT execution_id FROM execution_settlements WHERE cleanup_confirmed=0 AND processes_gone=1')]
            if targets and not unresolved and not running and not _execution_controls:
                try:
                    cleanup_after_run(WORK_DIR)
                except Exception as exc:
                    for execution_id in targets:
                        _update_conn(conn, execution_id, workspace_cleanup_confirmed=0, note=str(exc)[:1000])
                else:
                    for execution_id in targets:
                        _update_conn(conn, execution_id, workspace_cleanup_confirmed=1, runner_returned=1)
    with transaction() as conn:
        return [row['execution_id'] for row in conn.execute('SELECT execution_id FROM execution_settlements WHERE cleanup_confirmed=0')]


def _launcher() -> int:
    if len(sys.argv) != 4 or sys.argv[1] != '--launch':
        return 2
    token, script = sys.argv[2:]
    if sys.stdin.buffer.readline(80).decode('ascii', errors='replace').strip() != token:
        return 125
    # Runs after assignment. All descendants inherit the no-breakaway Job.
    return subprocess.call([sys.executable, '-u', script], stdin=subprocess.DEVNULL)


if __name__ == '__main__':
    raise SystemExit(_launcher())
