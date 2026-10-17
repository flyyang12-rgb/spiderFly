from __future__ import annotations

import asyncio
import ctypes
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app import config, database, execution_results, native_process, runner


class SettlementFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_patch = patch.object(database, 'DB_PATH', self.root / 'test.sqlite')
        self.db_patch.start()
        self.work = self.root / 'work'
        self.work.mkdir()
        self.work_patch = patch.object(config, 'WORK_DIR', self.work)
        self.work_patch.start()
        with database.transaction() as conn:
            native_process.ensure_schema(conn)
            conn.execute('CREATE TABLE executions(id INTEGER PRIMARY KEY, status TEXT, started_at TEXT)')
            conn.execute('CREATE TABLE cron_rounds(execution_id INTEGER, rerun_execution_id INTEGER)')

    def tearDown(self):
        self.work_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def row(self, execution_id=11):
        with database.transaction() as conn:
            return dict(conn.execute('SELECT * FROM execution_settlements WHERE execution_id=?', (execution_id,)).fetchone())

    def confirmed(self, execution_id=11):
        with database.transaction() as conn:
            return native_process.cleanup_confirmed(conn, execution_id)


class SettlementTests(SettlementFixture):
    def test_schema_and_confirmation_require_all_three_facts(self):
        with database.transaction() as conn:
            native_process.ensure_schema(conn)
            self.assertFalse(native_process.cleanup_confirmed(conn, 11))
        native_process.begin_settlement(11)
        native_process.mark_processes_gone(11)
        native_process.mark_workspace_cleanup(11, True)
        self.assertFalse(self.confirmed())
        native_process.mark_runner_returned(11)
        self.assertTrue(self.confirmed())

    def test_terminal_database_status_does_not_confirm_external_cleanup(self):
        native_process.start_external_settlement(11, self.root / 'missing.json', 'identity')
        with database.transaction() as conn:
            conn.execute("INSERT INTO executions VALUES(11,'failed',NULL)")
        pending = native_process.recover_settlements(clean_workspace=True)
        self.assertEqual(pending, [11])
        self.assertFalse(self.confirmed())
        self.assertEqual(self.row()['state'], 'uncertain')

    def test_wrong_external_token_and_incomplete_receipt_never_release(self):
        receipt = self.root / 'cleanup.json'
        native_process.start_external_settlement(11, receipt, 'right-token')
        for content in ('not-json', json.dumps({'token': 'wrong', 'complete': True}),
                        json.dumps({'token': 'right-token', 'complete': False})):
            receipt.write_text(content, encoding='utf-8')
            self.assertFalse(native_process.verify_external_receipt(11))
            self.assertFalse(self.confirmed())

    def test_matching_external_receipt_requires_workspace_and_return_boundary(self):
        receipt = self.root / 'cleanup.json'
        native_process.start_external_settlement(11, receipt, 'right-token')
        receipt.write_text(json.dumps({'token': 'right-token', 'complete': True}), encoding='utf-8')
        self.assertTrue(native_process.verify_external_receipt(11))
        native_process.mark_runner_returned(11)
        native_process.mark_workspace_cleanup(11, False, 'locked')
        self.assertFalse(self.confirmed())
        native_process.mark_workspace_cleanup(11, True)
        self.assertTrue(self.confirmed())

    def test_external_start_reuses_only_a_pristine_prepared_identity(self):
        name = native_process.begin_settlement(11)
        native_process.start_external_settlement(11, self.root / 'cleanup.json', 'identity')
        self.assertEqual(self.row()['job_name'], name)
        self.assertEqual(self.row()['state'], 'external_started')
        with self.assertRaises(RuntimeError):
            native_process.start_external_settlement(11, self.root / 'another.json', 'other')

    def test_recovery_cleanup_is_opt_in_and_rejects_other_running_execution(self):
        native_process.begin_settlement(11)
        native_process.mark_processes_gone(11)
        (self.work / 'owned.txt').write_text('synthetic', encoding='utf-8')
        native_process.recover_settlements()
        self.assertTrue((self.work / 'owned.txt').exists())
        with database.transaction() as conn:
            conn.execute("INSERT INTO executions VALUES(12,'running',NULL)")
        native_process.recover_settlements(clean_workspace=True)
        self.assertFalse(self.confirmed())
        self.assertTrue((self.work / 'owned.txt').exists())
        with database.transaction() as conn:
            conn.execute("UPDATE executions SET status='failed' WHERE id=12")
        native_process.recover_settlements(clean_workspace=True)
        self.assertTrue(self.confirmed())
        self.assertFalse((self.work / 'owned.txt').exists())

    def test_cron_association_includes_repair_rerun_without_affecting_legacy(self):
        with database.transaction() as conn:
            conn.execute('INSERT INTO cron_rounds VALUES(11,12)')
            self.assertTrue(native_process.cron_owned(conn, 11))
            self.assertTrue(native_process.cron_owned(conn, 12))
            self.assertFalse(native_process.cron_owned(conn, 13))

    def test_unstarted_confirmation_refuses_claimed_execution_and_existing_identity(self):
        with database.transaction() as conn:
            conn.execute('INSERT INTO cron_rounds VALUES(11,12)')
            conn.execute("INSERT INTO executions VALUES(11,'running',NULL)")
            conn.execute("INSERT INTO executions VALUES(12,'cancelled',NULL)")
            with self.assertRaises(ValueError):
                native_process.confirm_unstarted(conn, 11)
            self.assertTrue(native_process.confirm_unstarted(conn, 12))
            self.assertTrue(native_process.cleanup_confirmed(conn, 12))
            with self.assertRaises(ValueError):
                native_process.confirm_unstarted(conn, 12)
            self.assertFalse(native_process.confirm_unstarted(conn, 13))

    def test_unstarted_confirmation_rejects_pending_or_previously_started_cancellation(self):
        with database.transaction() as conn:
            conn.execute('INSERT INTO cron_rounds VALUES(11,12)')
            conn.execute("INSERT INTO executions VALUES(11,'pending',NULL)")
            conn.execute("INSERT INTO executions VALUES(12,'cancelled','2026-10-13T00:00:00Z')")
            for execution_id in (11, 12):
                with self.assertRaises(ValueError):
                    native_process.confirm_unstarted(conn, execution_id)

    def test_recovery_does_not_clean_a_live_runner_after_database_terminal_state(self):
        native_process.begin_settlement(11)
        native_process.mark_processes_gone(11)
        (self.work / 'owned.txt').write_text('synthetic', encoding='utf-8')
        with patch.dict(runner._execution_controls, {11: runner.ExecutionControl()}):
            native_process.recover_settlements(clean_workspace=True)
        self.assertFalse(self.confirmed())
        self.assertTrue((self.work / 'owned.txt').exists())


@unittest.skipUnless(os.name == 'nt', 'Windows Job Object requires Windows')
class WindowsOwnedProcessTests(SettlementFixture):
    def _run_with_transient_launcher_identity(self, scenario):
        original = native_process._identity_gone
        observed = []
        def identity(row):
            if not observed:
                observed.append(False)
                self.assertFalse(self.row()['processes_gone'])
                return False
            actual = original(row)
            observed.append(actual)
            return actual
        with patch.object(native_process, '_identity_gone', side_effect=identity):
            scenario()
        self.assertGreaterEqual(len(observed), 2)
        self.assertFalse(observed[0])
        self.assertTrue(observed[-1])

    def test_cron_force_stop_waits_for_transient_launcher_identity(self):
        self._run_with_transient_launcher_identity(
            self.test_cron_force_stop_confirms_owned_tree_and_workspace)

    def test_cron_service_cancellation_waits_for_transient_launcher_identity(self):
        self._run_with_transient_launcher_identity(
            self.test_cron_service_cancellation_records_receipt_after_runner_unwinds)

    def test_persistently_unconfirmed_launcher_exhausts_shared_budget_without_receipt(self):
        name = native_process.begin_settlement(11)
        job = native_process.OwnedJob(11, name)
        clock = SimpleNamespace(monotonic=MagicMock(side_effect=[100.0, 113.0]))
        try:
            with patch.object(native_process, '_identity_gone', return_value=False), patch.object(native_process, 'time', clock):
                with self.assertRaisesRegex(RuntimeError, '启动进程尚未确认退出'):
                    asyncio.run(job.finish())
        finally:
            job.close()
        self.assertFalse(self.row()['processes_gone'])
        self.assertFalse(self.confirmed())

    def test_restart_refuses_unidentified_launcher_and_matching_live_process(self):
        native_process.begin_settlement(11)
        native_process._update(11, state='launching')
        self.assertEqual(native_process.recover_settlements(clean_workspace=True), [11])
        self.assertFalse(self.confirmed())
        kernel = native_process._kernel()
        handle = kernel.OpenProcess(0x1000, False, os.getpid())
        try:
            created = native_process._creation_time(kernel, handle)
        finally:
            kernel.CloseHandle(handle)
        native_process._update(11, state='active', pid=os.getpid(), process_created_at=created)
        self.assertEqual(native_process.recover_settlements(clean_workspace=True), [11])
        self.assertFalse(self.row()['processes_gone'])

    def test_pid_reuse_is_recognized_without_terminating_unrelated_process(self):
        native_process.begin_settlement(11)
        native_process._update(11, state='active', pid=os.getpid(), process_created_at='older-identity')
        self.assertEqual(native_process.recover_settlements(clean_workspace=True), [])
        self.assertTrue(self.confirmed())

    def test_inaccessible_job_cannot_be_reported_as_gone(self):
        native_process.begin_settlement(11)
        kernel = MagicMock()
        kernel.OpenJobObjectW.return_value = None
        with patch.object(native_process, '_kernel', return_value=kernel), patch.object(ctypes, 'get_last_error', return_value=5):
            self.assertEqual(native_process.recover_settlements(clean_workspace=True), [11])
        self.assertFalse(self.confirmed())
        self.assertFalse(self.row()['processes_gone'])

    def runner_fixture(self, stack, source):
        apps = self.root / 'apps'
        script_dir = apps / '5'
        script_dir.mkdir(parents=True)
        script = script_dir / 'main.py'
        script.write_text(source, encoding='utf-8')
        interpreter = Path(sys.executable).resolve()
        task = {'id': 5, 'name': 'synthetic Cron', 'script_path_snapshot': str(script),
                'python_path_snapshot': str(interpreter),
                'notify_on_success': 0, 'notify_on_failure': 0}
        with database.transaction() as conn:
            conn.execute('INSERT INTO cron_rounds VALUES(11,NULL)')
            conn.execute("INSERT INTO executions VALUES(11,'failed',NULL)")
        for target, field, value in (
            (runner, 'RPA_APPS_DIR', apps), (runner, 'RPA_ENVS_DIR', interpreter.parents[1]),
            (runner, 'WORK_DIR', self.work), (execution_results, 'EXECUTIONS_DIR', self.root / 'executions')):
            stack.enter_context(patch.object(target, field, value))
        stack.enter_context(patch.object(runner, 'fetch_one', return_value=task))
        stack.enter_context(patch.object(runner, 'append_execution_output'))
        calls = stack.enter_context(patch.object(runner, 'execute'))
        return calls

    def test_cron_runner_waits_for_notifications_before_durable_confirmation(self):
        marker = self.root / 'child.pid'
        source = ('import subprocess,sys,os\nfrom pathlib import Path\n'
                  'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
                  f'Path({str(marker)!r}).write_text(str(p.pid))\n'
                  'Path(os.environ["SPIDERFLY_WORK_DIR"],"partial.txt").write_text("synthetic")\n')
        async def notification(*args, **kwargs):
            self.assertTrue(self.row()['processes_gone'])
            self.assertTrue(self.row()['workspace_cleanup_confirmed'])
            self.assertFalse(self.confirmed())
            native_process.recover_settlements(clean_workspace=True)
            self.assertFalse(self.confirmed())
        with ExitStack() as stack:
            calls = self.runner_fixture(stack, source)
            stack.enter_context(patch.object(runner, '_send_notification', side_effect=notification))
            asyncio.run(runner.run_execution(11))
        self.assertTrue(marker.exists())
        self.assertTrue(self.confirmed())
        self.assertEqual(list(self.work.iterdir()), [])
        final = next(call.args[1] for call in calls.call_args_list if 'result_source' in call.args[0])
        self.assertEqual(final[0], 'success')

    def test_cron_force_stop_confirms_owned_tree_and_workspace(self):
        ready = self.root / 'ready.txt'
        source = ('import subprocess,sys,time\nfrom pathlib import Path\n'
                  'subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
                  f'Path({str(ready)!r}).write_text("started")\n'
                  'time.sleep(60)\n')
        async def run():
            execution = asyncio.create_task(runner.run_execution(11))
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and not execution.done() and time.monotonic() < deadline:
                    await asyncio.sleep(0.03)
                self.assertTrue(ready.exists())
                self.assertTrue(runner.request_execution_stop(11, 'synthetic tester'))
                await asyncio.wait_for(execution, 8)
            finally:
                if not execution.done():
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
        with ExitStack() as stack:
            calls = self.runner_fixture(stack, source)
            stack.enter_context(patch.object(runner, '_send_notification', new=AsyncMock()))
            asyncio.run(run())
        self.assertTrue(self.confirmed())
        final = next(call.args[1] for call in calls.call_args_list if 'result_source' in call.args[0])
        self.assertEqual(final[0], 'cancelled')

    def test_cron_service_cancellation_records_receipt_after_runner_unwinds(self):
        ready = self.root / 'ready.txt'
        source = ('import subprocess,sys,time\nfrom pathlib import Path\n'
                  'subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
                  f'Path({str(ready)!r}).write_text("started")\n'
                  'time.sleep(60)\n')
        async def run():
            execution = asyncio.create_task(runner.run_execution(11))
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and not execution.done() and time.monotonic() < deadline:
                    await asyncio.sleep(0.03)
                self.assertTrue(ready.exists())
                execution.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(execution, 8)
            finally:
                if not execution.done():
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
        with ExitStack() as stack:
            calls = self.runner_fixture(stack, source)
            asyncio.run(run())
        self.assertTrue(self.confirmed())
        self.assertNotIn(11, runner._execution_controls)
        final = next(call.args[1] for call in calls.call_args_list if 'result_source' in call.args[0])
        self.assertEqual(final[0], 'cancelled')

    def test_cron_preparation_failure_confirms_no_process_was_started(self):
        with ExitStack() as stack:
            calls = self.runner_fixture(stack, 'print("must-not-run")\n')
            stack.enter_context(patch.object(runner, 'validate_script', side_effect=FileNotFoundError('synthetic missing script')))
            spawn = stack.enter_context(patch.object(runner.asyncio, 'create_subprocess_exec', new=AsyncMock()))
            stack.enter_context(patch.object(runner, '_send_notification', new=AsyncMock()))
            asyncio.run(runner.run_execution(11))
            spawn.assert_not_awaited()
        self.assertTrue(self.confirmed())
        final = next(call.args[1] for call in calls.call_args_list if 'result_source' in call.args[0])
        self.assertEqual(final[0], 'failed')

    def test_parent_exit_kills_and_confirms_owned_descendant_with_inherited_pipes(self):
        script = self.root / 'task.py'
        marker = self.work / 'child.pid'
        script.write_text(
            'import subprocess,sys\n'
            'from pathlib import Path\n'
            'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
            f'Path({str(marker)!r}).write_text(str(p.pid))\n'
            'print("parent-success",flush=True)\n', encoding='utf-8')
        name = native_process.begin_settlement(11)
        job = native_process.OwnedJob(11, name)

        async def run():
            process = await native_process.launch_owned(11, job, sys.executable, script,
                                                        cwd=str(self.root), env=os.environ.copy())
            try:
                await runner._wait_for_process(process, runner.ExecutionControl(), 5, parent_exit_only=True)
                self.assertEqual(process.returncode, 0)
                await job.finish()
                await asyncio.wait_for(process.wait(), 5)
                self.assertEqual(job.active_count(), 0)
            finally:
                job.close()
                if process.returncode is None:
                    process.kill()
                await asyncio.wait_for(process.wait(), 5)
        asyncio.run(run())
        self.assertTrue(marker.exists())
        self.assertTrue(native_process.processes_gone(11))

    def test_assignment_failure_does_not_execute_business_code(self):
        marker = self.work / 'should-not-exist.txt'
        script = self.root / 'task.py'
        script.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n', encoding='utf-8')
        name = native_process.begin_settlement(11)
        job = native_process.OwnedJob(11, name)
        async def run():
            try:
                with patch.object(job, 'assign', side_effect=OSError('synthetic assign failure')):
                    with self.assertRaises(OSError):
                        await native_process.launch_owned(11, job, sys.executable, script,
                                                          cwd=str(self.root), env=os.environ.copy())
            finally:
                job.close()
        asyncio.run(run())
        self.assertFalse(marker.exists())
        self.assertTrue(native_process.processes_gone(11))

    def test_stop_before_gate_does_not_start_user_script(self):
        marker = self.work / 'should-not-exist.txt'
        script = self.root / 'task.py'
        script.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n', encoding='utf-8')
        name = native_process.begin_settlement(11)
        job = native_process.OwnedJob(11, name)
        async def run():
            def stopped():
                raise runner.ForceStopRequested('synthetic stop')
            try:
                with self.assertRaises(runner.ForceStopRequested):
                    await native_process.launch_owned(11, job, sys.executable, script,
                        cwd=str(self.root), env=os.environ.copy(), before_gate=stopped)
            finally:
                job.close()
        asyncio.run(run())
        self.assertFalse(marker.exists())
        self.assertTrue(native_process.processes_gone(11))

    def test_owner_crash_kills_owned_tree_and_recovery_confirms_only_after_inspection(self):
        ready = self.root / 'ready.txt'
        script = self.root / 'business.py'
        script.write_text('import time\nfrom pathlib import Path\n'
                          f'Path({str(ready)!r}).write_text("started")\n'
                          'time.sleep(60)\n', encoding='utf-8')
        owner = self.root / 'owner.py'
        backend_root = Path(native_process.__file__).resolve().parents[1]
        owner.write_text(
            'import asyncio,sys,os\nfrom pathlib import Path\n'
            f'sys.path.insert(0,{str(backend_root)!r})\n'
            'from app import database,native_process\n'
            f'database.DB_PATH=Path({str(self.root / "test.sqlite")!r})\n'
            'async def run():\n'
            ' name=native_process.begin_settlement(11)\n'
            ' job=native_process.OwnedJob(11,name)\n'
            f' p=await native_process.launch_owned(11,job,sys.executable,Path({str(script)!r}),cwd={str(self.root)!r},env=os.environ.copy())\n'
            ' await asyncio.sleep(60)\n'
            'asyncio.run(run())\n', encoding='utf-8')
        owner_process = subprocess.Popen([sys.executable, '-I', str(owner)],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 8
            while not ready.exists() and owner_process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.03)
            if not ready.exists():
                owner_process.kill()
                _, stderr = owner_process.communicate(timeout=5)
                self.fail('owner did not start synthetic task: ' + stderr.decode('utf-8', errors='replace'))
            owner_process.kill()
            owner_process.wait(timeout=5)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                native_process.recover_settlements(clean_workspace=True)
                if self.confirmed():
                    break
                time.sleep(0.03)
            self.assertTrue(self.confirmed())
        finally:
            if owner_process.poll() is None:
                owner_process.kill()
            owner_process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
