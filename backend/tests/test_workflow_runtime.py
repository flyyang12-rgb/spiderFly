from __future__ import annotations

import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app import config, database, environments, execution_results, maintenance, native_process, runner, task_versions
from app.services import workflows
from tests import test_maintenance as maintenance_fixtures


class WorkflowSettlementTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(database, 'DB_PATH', self.root / 'test.sqlite'))
        with database.transaction() as conn:
            native_process.ensure_schema(conn)
            conn.execute('CREATE TABLE executions(id INTEGER PRIMARY KEY,status TEXT,started_at TEXT)')
            conn.execute('CREATE TABLE workflow_steps(execution_id INTEGER UNIQUE)')
            conn.execute('INSERT INTO workflow_steps VALUES(31)')

    def test_workflow_association_opts_in_without_migrating_legacy_execution(self):
        with database.transaction() as conn:
            self.assertFalse(native_process.cron_owned(conn, 31))
            self.assertTrue(native_process.workflow_owned(conn, 31))
            self.assertTrue(native_process.coordinated_owned(conn, 31))
            self.assertFalse(native_process.coordinated_owned(conn, 32))
        self.assertTrue(native_process.is_workflow_execution(31))
        self.assertTrue(native_process.is_coordinated_execution(31))

    def test_workflow_pending_cancellation_requires_never_started_identity(self):
        with database.transaction() as conn:
            conn.execute("INSERT INTO executions VALUES(31,'pending',NULL)")
            with self.assertRaises(ValueError):
                native_process.confirm_unstarted(conn, 31)
            conn.execute("UPDATE executions SET status='cancelled' WHERE id=31")
            self.assertTrue(native_process.confirm_unstarted(conn, 31))
            self.assertTrue(native_process.cleanup_confirmed(conn, 31))
            with self.assertRaises(ValueError):
                native_process.confirm_unstarted(conn, 31)

    def test_failed_workflow_never_enters_old_maintenance_or_business_rerun(self):
        # The guard runs before reading even a legacy maintenance snapshot or AI settings.
        with patch.object(maintenance, 'available', side_effect=AssertionError('must not inspect old maintenance')):
            maintenance.record_failure(31)
        with database.transaction() as conn:
            with self.assertRaises(InterruptedError):
                maintenance.activate(conn, {'execution_id': 31}, {}, {})
            with self.assertRaises(InterruptedError):
                maintenance._enqueue_repaired_run(conn, {'execution_id': 31}, {}, {}, {}, [], 120)
        with patch.object(maintenance, 'fetch_one', return_value={
            'execution_id': 31, 'stop_requested': 0, 'status': 'ready', 'snapshot': 'invalid'
        }):
            with self.assertRaises(InterruptedError):
                maintenance.current_job(1)


@unittest.skipUnless(os.name == 'nt', 'Windows Job Object requires Windows')
class WorkflowRunnerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.work = self.root / 'work'
        self.work.mkdir()
        self.stack.enter_context(patch.object(database, 'DB_PATH', self.root / 'test.sqlite'))
        with database.transaction() as conn:
            native_process.ensure_schema(conn)
            conn.execute('CREATE TABLE executions(id INTEGER PRIMARY KEY,status TEXT,started_at TEXT)')
            conn.execute('CREATE TABLE workflow_steps(execution_id INTEGER UNIQUE)')
            conn.execute('INSERT INTO workflow_steps VALUES(31)')
            conn.execute("INSERT INTO executions VALUES(31,'running',NULL)")
        self.script = self.root / 'workflow_snapshots' / 'frozen-round' / '1' / 'main.py'
        self.script.parent.mkdir(parents=True)
        self.script.write_text('print("frozen-source")\n', encoding='utf-8')
        self.template = self.script.parent / 'template.xlsx'
        self.template.write_text('frozen-template', encoding='utf-8')
        self.frozen = {
            'id': 5, 'name': 'frozen name', 'runtime': 'native', 'target_host_id': None,
            'script_path_snapshot': str(self.script), 'python_path_snapshot': sys.executable,
            'template_filename': 'frozen.xlsx', 'template_path': str(self.template),
            'notify_on_success': 0, 'notify_on_failure': 0, 'failure_screenshot': 0,
            'timeout_seconds': 120, 'browser_port': 9137,
        }
        self.live = {
            **self.frozen, 'name': 'current changed name',
            'script_path_snapshot': str(self.root / 'current-missing.py'),
            'template_path': str(self.root / 'current-missing.xlsx'),
            'timeout_seconds': 600,
            # A changed live policy must not route the accepted native step into a dedicated runtime.
            'maintenance_snapshot': '{"policy":{"runtime":"readonly-v1"}}',
        }
        for module, key, value in (
            (runner, 'DATA_DIR', self.root), (runner, 'WORK_DIR', self.work),
            (config, 'WORK_DIR', self.work),
            (runner, 'RPA_ENVS_DIR', Path(sys.executable).resolve().parents[1]),
            (execution_results, 'EXECUTIONS_DIR', self.root / 'executions'),
        ):
            self.stack.enter_context(patch.object(module, key, value))
        self.stack.enter_context(patch.object(runner, 'fetch_one', return_value=self.live))
        self.updates = self.stack.enter_context(patch.object(runner, 'execute'))
        self.output = self.stack.enter_context(patch.object(runner, 'append_execution_output'))
        self.snapshot = self.stack.enter_context(patch.object(runner, '_workflow_execution_snapshot', return_value=self.frozen))
        self.allowed = self.stack.enter_context(patch.object(runner, '_check_workflow_allowed'))
        self.notification = self.stack.enter_context(patch.object(runner, '_send_notification', new=AsyncMock()))
        self.stack.enter_context(patch.object(maintenance, 'capture_snapshot', side_effect=AssertionError('must not recapture')))

    def receipt(self):
        with database.transaction() as conn:
            return dict(conn.execute('SELECT * FROM execution_settlements WHERE execution_id=31').fetchone())

    def final_status(self):
        return next(call.args[1][0] for call in self.updates.call_args_list if 'result_source' in call.args[0])

    def test_runner_uses_frozen_source_template_parameters_and_timeout(self):
        self.script.write_text(
            'import os\nfrom pathlib import Path\n'
            'print("frozen-source:"+Path(os.environ["SPIDERFLY_TEMPLATE_FILE"]).read_text())\n'
            'print("frozen-port:"+os.environ["SPIDERFLY_BROWSER_PORT"])\n',
            encoding='utf-8',
        )
        with patch.object(runner, '_wait_for_process', wraps=runner._wait_for_process) as waited:
            asyncio.run(runner.run_execution(31))
        self.snapshot.assert_called_once_with(31)
        self.assertEqual(waited.call_args.args[2], 120)
        self.assertTrue(waited.call_args.kwargs['parent_exit_only'])
        self.assertEqual(self.notification.call_args.args[1]['name'], 'frozen name')
        self.assertIn('frozen-source:frozen-template', ''.join(c.args[2] for c in self.output.call_args_list if c.args[1] == 'stdout'))
        self.assertIn('frozen-port:9137', ''.join(c.args[2] for c in self.output.call_args_list if c.args[1] == 'stdout'))
        self.assertEqual(self.final_status(), 'success')
        self.assertTrue(self.receipt()['cleanup_confirmed'])
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertEqual(self.allowed.call_count, 2)

    def test_snapshot_failure_never_launches_and_still_confirms_no_start_cleanup(self):
        self.snapshot.side_effect = ValueError('冻结源码哈希已改变')
        with patch.object(native_process, 'launch_owned', new=AsyncMock()) as launch:
            asyncio.run(runner.run_execution(31))
        launch.assert_not_called()
        self.assertEqual(self.final_status(), 'failed')
        self.assertTrue(self.receipt()['cleanup_confirmed'])
        self.assertTrue(self.receipt()['processes_gone'])
        self.assertTrue(self.receipt()['runner_returned'])

    def test_invalid_frozen_timeout_never_launches_business_code(self):
        self.frozen['timeout_seconds'] = '600'
        with patch.object(native_process, 'launch_owned', new=AsyncMock()) as launch:
            asyncio.run(runner.run_execution(31))
        launch.assert_not_called()
        self.assertEqual(self.final_status(), 'failed')
        self.assertTrue(self.receipt()['cleanup_confirmed'])

    def test_persisted_stop_during_gated_launch_wins_without_running_script(self):
        marker = self.root / 'must-not-start.txt'
        self.script.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n', encoding='utf-8')
        def check(execution_id, control):
            if self.allowed.call_count == 2:
                control.reason = '流程已请求停止'
                control.stop_event.set()
                raise runner.ForceStopRequested(control.reason)
        self.allowed.side_effect = check
        asyncio.run(runner.run_execution(31))
        self.assertFalse(marker.exists())
        self.assertEqual(self.final_status(), 'cancelled')
        self.assertTrue(self.receipt()['cleanup_confirmed'])

    def test_public_cleanup_failure_cannot_be_reported_as_success(self):
        with patch.object(runner, 'cleanup_after_run', side_effect=OSError('synthetic locked directory')):
            asyncio.run(runner.run_execution(31))
        self.assertEqual(self.final_status(), 'failed')
        self.assertFalse(self.receipt()['cleanup_confirmed'])
        self.assertTrue(self.receipt()['processes_gone'])
        self.assertTrue(self.receipt()['runner_returned'])


class WorkflowVersionProtectionTests(unittest.TestCase):
    def setUp(self):
        maintenance_fixtures.MaintenanceTests.setUp(self)

    def test_manual_publication_changes_legacy_pending_but_preserves_workflow_pending(self):
        with database.transaction() as conn:
            snapshot = maintenance.capture_snapshot(conn, self.task_id)
            current = maintenance.task_record(conn, self.task_id)
            candidate = maintenance.version(conn, current, 'print("new-version")\n', approved=True, kind='manual')
            task_versions.ensure_details(conn, current, candidate, profile='native')
            flow = conn.execute("INSERT INTO executions(task_id,status,script_path_snapshot,maintenance_snapshot,created_at) VALUES(?,'pending',?,'frozen-flow-json',?)",
                                (self.task_id, str(self.path), database.utc_now())).lastrowid
            legacy = conn.execute("INSERT INTO executions(task_id,status,trigger_source,script_path_snapshot,maintenance_snapshot,created_at) VALUES(?,'pending','schedule',?,?,?)",
                                  (self.task_id, str(self.path), snapshot, database.utc_now())).lastrowid
            with patch.object(native_process, 'workflow_owned', side_effect=lambda db_conn, eid: eid == flow):
                task_versions.publish(conn, self.task_id, candidate['id'])
        frozen = database.fetch_one('SELECT script_path_snapshot,maintenance_snapshot FROM executions WHERE id=?', (flow,))
        changed = database.fetch_one('SELECT script_path_snapshot,maintenance_snapshot FROM executions WHERE id=?', (legacy,))
        self.assertEqual(frozen, {'script_path_snapshot': str(self.path), 'maintenance_snapshot': 'frozen-flow-json'})
        self.assertEqual(changed['script_path_snapshot'], candidate['path'])
        self.assertNotEqual(changed['maintenance_snapshot'], snapshot)

    def test_old_automatic_repair_never_rebinds_or_reuses_workflow_pending(self):
        maintenance_fixtures.MaintenanceTests.fail_execution(self)
        job = database.fetch_one('SELECT * FROM maintenance_jobs ORDER BY id DESC LIMIT 1')
        flow = database.execute("INSERT INTO executions(task_id,status,script_path_snapshot,maintenance_snapshot,created_at) VALUES(?,'pending',?,'frozen-flow-json',?)",
                                (self.task_id, str(self.path), database.utc_now()))
        legacy = database.execute("INSERT INTO executions(task_id,status,trigger_source,script_path_snapshot,maintenance_snapshot,created_at) VALUES(?,'pending','schedule',?,?,?)",
                                  (self.task_id, str(self.path), job['snapshot'], database.utc_now()))
        # Reuse the fixture operations without importing another TestCase class into discovery.
        self.job = lambda: database.fetch_one('SELECT * FROM maintenance_jobs ORDER BY id DESC LIMIT 1')
        with patch.object(native_process, 'workflow_owned', side_effect=lambda db_conn, eid: eid == flow):
            maintenance_fixtures.MaintenanceTests.generate(self)
            with patch.object(maintenance, 'execute_profile', side_effect=AssertionError('must yield before validating')):
                self.assertFalse(asyncio.run(maintenance.run_next_trial()))
            self.assertEqual(self.job()['status'], 'ready')
            self.assertIsNone(self.job()['rerun_execution_id'])
            with database.transaction() as conn:
                with self.assertRaises(BlockingIOError):
                    maintenance.activate(conn, self.job(), {}, {})
                with self.assertRaises(BlockingIOError):
                    maintenance._enqueue_repaired_run(conn, self.job(), {'id': self.task_id}, {}, {}, [flow, legacy], 120)
            database.execute("UPDATE executions SET status='cancelled' WHERE id=?", (flow,))
            maintenance_fixtures.MaintenanceTests.trial(self)
        self.assertEqual(database.fetch_one('SELECT maintenance_snapshot FROM executions WHERE id=?', (flow,))['maintenance_snapshot'], 'frozen-flow-json')
        self.assertEqual(self.job()['rerun_execution_id'], legacy)
        self.assertNotEqual(database.fetch_one('SELECT script_path_snapshot FROM executions WHERE id=?', (legacy,))['script_path_snapshot'], str(self.path))


@unittest.skipUnless(os.name == 'nt', 'Windows Job Object requires Windows')
class WorkflowCoreRunnerTests(unittest.TestCase):
    def setUp(self):
        maintenance_fixtures.MaintenanceTests.setUp(self)
        self.work = self.root / 'work'
        self.work.mkdir()
        interpreter = Path(sys.executable).resolve()
        for module, key, value in (
            (workflows, 'SNAPSHOT_ROOT', self.root / 'workflow_snapshots'),
            (workflows, 'RPA_APPS_DIR', self.root / 'apps'),
            (workflows, 'RPA_ENVS_DIR', interpreter.parents[1]),
            (runner, 'DATA_DIR', self.root), (runner, 'WORK_DIR', self.work),
            (config, 'WORK_DIR', self.work), (runner, 'RPA_ENVS_DIR', interpreter.parents[1]),
        ):
            self.stack.enter_context(patch.object(module, key, value))
        self.stack.enter_context(patch.object(environments, 'managed_runtime_paths',
            side_effect=lambda app: (Path(app['script_path']), interpreter)))
        self.capture = self.stack.enter_context(patch.object(maintenance, 'capture_snapshot', side_effect=AssertionError('workflow must be independent of AI snapshot')))
        self.path.write_text('print("first-frozen")\n', encoding='utf-8')
        now = database.utc_now()
        self.second_app = database.execute("INSERT INTO rpa_apps(name,environment_status,created_at,updated_at) VALUES('第二步','ready',?,?)", (now, now))
        self.second_path = self.root / 'apps' / str(self.second_app) / 'main.py'
        self.second_path.parent.mkdir()
        self.second_path.write_text('print("second-frozen")\n', encoding='utf-8')
        self.second_task = database.execute("INSERT INTO tasks(name,app_id,script_path,created_by,notify_on_success,notify_on_failure,created_at,updated_at) VALUES('第二步',?,?,?,0,0,?,?)",
            (self.second_app, str(self.second_path), self.user['id'], now, now))
        database.execute("UPDATE rpa_apps SET script_path=?,environment_status='ready' WHERE id=?", (str(self.path), self.app_id))
        database.execute('UPDATE rpa_apps SET script_path=? WHERE id=?', (str(self.second_path), self.second_app))
        with database.transaction() as conn:
            workflows.ensure_schema(conn)
            native_process.ensure_schema(conn)
            self.workflow = workflows.create_definition(conn, '本机冻结串行验收', '', [self.task_id, self.second_task], self.user['id'])

    def start(self):
        return workflows.start_run(self.workflow['id'], self.user['id'], 'runtime-check')

    def execute_current(self, run_id):
        with database.transaction() as conn:
            step = conn.execute("SELECT execution_id FROM workflow_steps WHERE run_id=? AND state='pending'", (run_id,)).fetchone()
            self.assertIsNotNone(step)
            execution_id = step['execution_id']
            conn.execute("UPDATE executions SET status='running',started_at=NULL WHERE id=?", (execution_id,))
        asyncio.run(runner.run_execution(execution_id))
        workflows.reconcile()
        with database.transaction() as conn:
            self.assertTrue(native_process.cleanup_confirmed(conn, execution_id))
        return execution_id

    def get_run(self, run_id):
        with database.transaction() as conn:
            return workflows.get_run(conn, run_id)

    def test_real_runner_advances_frozen_steps_without_ai_or_current_file_recapture(self):
        accepted = self.start()
        # The yet-to-run step changes after whole-round acceptance.
        self.second_path.write_text('raise RuntimeError("current-source-must-not-run")\n', encoding='utf-8')
        database.execute("UPDATE tasks SET name='后来修改的名称',version=version+1 WHERE id=?", (self.second_task,))
        first = self.execute_current(accepted['id'])
        second = self.execute_current(accepted['id'])
        self.assertNotEqual(first, second)
        self.assertEqual(database.fetch_one('SELECT stdout FROM executions WHERE id=?', (first,))['stdout'].strip(), 'first-frozen')
        self.assertEqual(database.fetch_one('SELECT stdout FROM executions WHERE id=?', (second,))['stdout'].strip(), 'second-frozen')
        result = self.get_run(accepted['id'])
        self.assertEqual(result['status'], 'success')
        self.assertEqual([step['status'] for step in result['steps']], ['success', 'success'])
        self.assertEqual(result['steps'][1]['task_name'], '第二步')
        self.capture.assert_not_called()
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM maintenance_jobs')['n'], 0)

    def test_manual_required_business_receipt_fails_round_without_downstream_or_repair(self):
        self.path.write_text(
            'import json,os\nfrom pathlib import Path\n'
            'Path(os.environ["SPIDERFLY_RESULT_FILE"]).write_text(json.dumps({"schema_version":1,"outcome":"manual_required","code":"LOGIN_EXPIRED","message":"需要重新登录","manual_code":"ACCOUNT_RELOGIN"}),encoding="utf-8")\n',
            encoding='utf-8',
        )
        accepted = self.start()
        execution_id = self.execute_current(accepted['id'])
        maintenance.record_failure(execution_id)
        result = self.get_run(accepted['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual([step['status'] for step in result['steps']], ['failed', 'skipped'])
        self.assertIsNone(result['steps'][1]['execution_id'])
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'], 1)
        self.assertEqual(database.fetch_one('SELECT business_outcome,result_code FROM executions WHERE id=?', (execution_id,)),
            {'business_outcome': 'manual_required', 'result_code': 'LOGIN_EXPIRED'})
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM maintenance_jobs')['n'], 0)

    def test_persisted_stop_at_real_launcher_gate_cancels_whole_round(self):
        marker = self.root / 'must-not-start.txt'
        self.path.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n', encoding='utf-8')
        accepted = self.start()
        original_launch = native_process.launch_owned
        async def launch(execution_id, job, interpreter, script, *, cwd, env, before_gate):
            def stopped_gate():
                workflows.request_stop(accepted['id'], '隔离验证')
                before_gate()
            return await original_launch(execution_id, job, interpreter, script,
                cwd=cwd, env=env, before_gate=stopped_gate)
        with patch.object(native_process, 'launch_owned', side_effect=launch):
            self.execute_current(accepted['id'])
        self.assertFalse(marker.exists())
        result = self.get_run(accepted['id'])
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual([step['status'] for step in result['steps']], ['cancelled', 'cancelled'])
        self.assertIsNone(result['steps'][1]['execution_id'])

    def test_snapshot_deleted_after_claim_fails_and_releases_only_with_cleanup_proof(self):
        accepted = self.start()
        execution_id = accepted['steps'][0]['execution_id']
        with database.transaction() as conn:
            frozen = workflows.execution_snapshot(conn, execution_id)
        Path(frozen['script_path_snapshot']).unlink()
        self.execute_current(accepted['id'])
        result = self.get_run(accepted['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual([step['status'] for step in result['steps']], ['failed', 'skipped'])
        self.assertIsNone(result['steps'][1]['execution_id'])
