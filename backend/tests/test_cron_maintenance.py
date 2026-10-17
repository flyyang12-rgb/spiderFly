"""Cron-owned repairs with real isolated SQLite and simulated model/runtime I/O.

These tests exercise the new Cron transaction and recovery path. They do not
claim WSL, business-side effects or a live model were verified.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import unittest
from unittest.mock import AsyncMock, patch

from app import ai_settings, database as db, maintenance as m, maintenance_runtime, native_process, runner
from app.api import schedules
from app.services import cron_runs, execution_queue
from tests import test_maintenance


class CronMaintenanceTests(unittest.TestCase):
    setUp = test_maintenance.MaintenanceTests.setUp
    generate = test_maintenance.MaintenanceTests.generate
    trial = test_maintenance.MaintenanceTests.trial
    job = test_maintenance.MaintenanceTests.job
    clock = '2026-10-14T01:00:00+00:00'

    def cron_failure(self, *, result_code='', snapshot=True):
        db.execute("""UPDATE tasks SET trigger_type='cron',trigger_config='{"expression":"* * * * *"}',
            next_run_at=?,created_at='2026-10-14T00:00:00+00:00' WHERE id=?""", (self.clock, self.task_id))
        record = cron_runs.register_due(self.task_id, self.clock)
        self.assertEqual(record['state'], 'pending')
        with patch.object(execution_queue, 'managed_runtime_paths', return_value=(self.path, sys.executable)):
            self.assertEqual(cron_runs.dispatch_pending(execution_queue._enqueue_task_in_transaction), 1)
        record = self.round()
        execution_id = record['execution_id']
        db.execute("""UPDATE executions SET status='failed',error_message='NameError: missing_value',
            stderr='NameError: missing_value',result_code=?,ended_at=? WHERE id=?""",
            (result_code, self.clock, execution_id))
        if not snapshot:
            db.execute("UPDATE executions SET maintenance_snapshot='' WHERE id=?", (execution_id,))
        self.assertEqual(record['maintenance_decision'], 'pending')
        return execution_id

    def round(self):
        return db.fetch_one("SELECT * FROM cron_rounds WHERE source='schedule' AND state!='skipped' ORDER BY id LIMIT 1")

    def confirm(self, execution_id):
        # Explicit synthetic cleanup facts; this is not evidence of WSL cleanup.
        with db.transaction() as conn:
            native_process.ensure_schema(conn)
            conn.execute("""INSERT OR REPLACE INTO execution_settlements
                (execution_id,job_name,processes_gone,workspace_cleanup_confirmed,runner_returned,
                 cleanup_confirmed,created_at,updated_at) VALUES(?,?,1,1,1,1,?,?)""",
                (execution_id, f'isolated-synthetic-{execution_id}', self.clock, self.clock))

    def assert_none(self, execution_id, code, fragment):
        with patch.object(ai_settings, 'model_request') as model:
            result = m.record_failure(execution_id)
            model.assert_not_called()
        self.assertEqual(result['decision'], 'none')
        record = self.round()
        self.assertEqual(record['maintenance_decision'], 'none')
        self.assertEqual(record['maintenance_reason_code'], code)
        self.assertIn(fragment, record['maintenance_reason'])
        self.assertEqual(record['execution_id'], execution_id)
        self.assertEqual(cron_runs.reconcile(now=self.clock), 0)
        self.confirm(execution_id)
        self.assertEqual(cron_runs.reconcile(now=self.clock), 1)
        entry = schedules.cron_history(self.task_id, None, {})['items'][0]
        self.assertEqual(entry['maintenance_reason_code'], code)
        self.assertIn(fragment, entry['maintenance_reason'])
        self.assertEqual(entry['maintenance_decision'], 'none')
        self.assertEqual(entry['state'], 'failed')
        return record['maintenance_reason']

    def test_missing_snapshot_reason_survives_reconcile_and_policy_changes(self):
        execution_id = self.cron_failure(snapshot=False)
        reason = self.assert_none(execution_id, 'snapshot_missing', '缺少冻结维护快照')
        db.execute("UPDATE maintenance_policies SET mode='off'")
        m.record_failure(execution_id)
        cron_runs.decide_maintenance(execution_id, reason='后来改变的策略', reason_code='different')
        self.assertEqual(self.round()['maintenance_reason'], reason)

    def test_disabled_policy_reason(self):
        execution_id = self.cron_failure()
        db.execute("UPDATE maintenance_policies SET mode='off'")
        self.assert_none(execution_id, 'policy_disabled', '策略已关闭')

    def test_missing_policy_reason(self):
        execution_id = self.cron_failure()
        db.execute('DELETE FROM maintenance_policies')
        self.assert_none(execution_id, 'policy_missing', '策略不存在')

    def test_changed_policy_reason(self):
        execution_id = self.cron_failure()
        db.execute('UPDATE maintenance_policies SET version=version+1')
        self.assert_none(execution_id, 'policy_changed', '策略已变更')

    def test_readonly_input_reason(self):
        execution_id = self.cron_failure(result_code='READONLY_INPUT_ERROR')
        self.assert_none(execution_id, 'readonly_input_error', '输入不可用')

    def test_force_stopped_reason(self):
        execution_id = self.cron_failure(result_code='FORCE_STOPPED')
        self.assert_none(execution_id, 'force_stopped', '强制停止')

    def test_removing_environment_reason(self):
        execution_id = self.cron_failure()
        db.execute("UPDATE rpa_apps SET environment_status='removing'")
        self.assert_none(execution_id, 'task_unavailable', '环境正在移除')

    def test_duplicate_failure_reason_is_observed_once_and_cannot_later_start_repair(self):
        # A prior ordinary failure completed its maintenance; the new actual
        # Cron-owned execution sees its same-version, same-signature fingerprint.
        prior = test_maintenance.MaintenanceTests.fail_execution(self)
        m.finish(self.job()['id'], 'review', '以前的同类故障已维护')
        execution_id = self.cron_failure()
        with patch.object(ai_settings, 'model_request') as model:
            result = m.record_failure(execution_id)
            self.assertEqual(result['reason_code'], 'duplicate_failure')
            db.execute('DELETE FROM maintenance_jobs WHERE execution_id=?', (prior,))
            self.assertEqual(m.record_failure(execution_id)['reason_code'], 'duplicate_failure')
            model.assert_not_called()
        self.assertEqual(db.fetch_one('SELECT COUNT(*) AS n FROM maintenance_jobs')['n'], 0)
        self.assert_none(execution_id, 'duplicate_failure', '24小时内已维护')

    def test_active_independent_maintenance_reason(self):
        execution_id = self.cron_failure()
        test_maintenance.MaintenanceTests.fail_execution(self, error='TypeError: different signature')
        self.assert_none(execution_id, 'maintenance_busy', '已有活动维护')

    def test_nonterminal_execution_cannot_commit_a_no_maintenance_decision(self):
        execution_id = self.cron_failure()
        db.execute("UPDATE executions SET status='running' WHERE id=?", (execution_id,))
        result = m.record_failure(execution_id)
        self.assertEqual(result['decision'], 'pending')
        cron_runs.decide_maintenance(execution_id)
        self.assertEqual(self.round()['maintenance_decision'], 'pending')
        self.assertEqual(self.round()['maintenance_reason'], '')

    def test_generation_trial_publish_create_new_rerun_in_original_cron_round(self):
        template = self.path.parent / 'input.xlsx'
        template.write_bytes(b'frozen synthetic input')
        db.execute('UPDATE rpa_apps SET template_path=?', (str(template),))
        execution_id = self.cron_failure()
        db.execute('UPDATE executions SET requested_by=? WHERE id=?', (self.users[1]['id'], execution_id))
        original = db.fetch_one('SELECT * FROM executions WHERE id=?', (execution_id,))
        result = m.record_failure(execution_id)
        self.assertEqual(result['decision'], 'entered')
        self.assertEqual(self.round()['maintenance_job_id'], self.job()['id'])
        self.confirm(execution_id)
        self.assertEqual(cron_runs.reconcile(now=self.clock), 0)
        model = self.generate()
        self.assertEqual(model.call_count, 1)
        # This compatible queue entry belongs to another request. It may follow
        # old version rebinding rules but cannot become this Cron round's rerun.
        other = db.execute("""INSERT INTO executions(task_id,status,maintenance_snapshot,
            trigger_source,requested_by,created_at) VALUES(?,'pending',?,'manual',?,?)""",
            (self.task_id, original['maintenance_snapshot'], self.user['id'], self.clock))
        self.trial()
        job, record = self.job(), self.round()
        rerun_id = job['rerun_execution_id']
        self.assertEqual(job['status'], 'activated')
        self.assertIsNone(job['ended_at'])
        self.assertNotIn(rerun_id, (execution_id, other))
        self.assertEqual(record['rerun_execution_id'], rerun_id)
        self.assertEqual(record['execution_id'], execution_id)
        self.assertEqual(record['scheduled_for'], self.clock)
        rerun = db.fetch_one('SELECT * FROM executions WHERE id=?', (rerun_id,))
        self.assertEqual(rerun['trigger_source'], 'maintenance')
        self.assertEqual(rerun['requested_by'], self.users[1]['id'])
        frozen = m.read_snapshot(rerun['maintenance_snapshot'])
        self.assertEqual(frozen['template'], m.read_snapshot(original['maintenance_snapshot'])['template'])
        self.assertEqual(frozen['source'], test_maintenance.GOOD)
        self.assertLessEqual(frozen['rerun_timeout_seconds'], 120)
        self.assertEqual(db.fetch_one('SELECT status FROM executions WHERE id=?', (execution_id,))['status'], 'failed')
        self.assertEqual(db.fetch_one('SELECT trigger_source FROM executions WHERE id=?', (other,))['trigger_source'], 'manual')
        self.assertEqual(cron_runs.register_due(self.task_id, '2026-10-14T01:02:00+00:00')['state'], 'skipped')
        # Startup and repeated reconcile retain the one pending repair execution.
        for _ in range(3):
            db.init_db(); m.init_tables(); m.reconcile_reruns()
            cron_runs.reconcile(now='2026-10-14T01:02:00+00:00', recover_decisions=True)
        self.assertEqual(self.round()['rerun_execution_id'], rerun_id)
        self.assertEqual(db.fetch_one("SELECT COUNT(*) AS n FROM executions WHERE trigger_source='maintenance'")['n'], 1)
        db.execute("UPDATE executions SET status='failed',ended_at=? WHERE id=?", (self.clock, rerun_id))
        m.record_failure(rerun_id)
        self.assertEqual(cron_runs.reconcile(now='2026-10-14T01:03:00+00:00'), 0)
        self.confirm(rerun_id)
        m.reconcile_reruns()
        self.assertEqual(cron_runs.reconcile(now='2026-10-14T01:03:00+00:00'), 1)
        self.assertEqual(self.round()['final_status'], 'failed')
        self.assertEqual(self.round()['rerun_status'], 'failed')
        self.assertEqual(db.fetch_one('SELECT COUNT(*) AS n FROM maintenance_jobs')['n'], 1)
        with patch.object(ai_settings, 'model_request') as replay:
            self.assertFalse(asyncio.run(m.generate_next()))
            replay.assert_not_called()

    def test_failure_decision_and_job_creation_rollback_together(self):
        execution_id = self.cron_failure()
        with patch.object(cron_runs, 'decide_maintenance', side_effect=RuntimeError('synthetic ledger write failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic'):
                m.record_failure(execution_id)
        self.assertEqual(db.fetch_one('SELECT COUNT(*) AS n FROM maintenance_jobs')['n'], 0)
        self.assertEqual(self.round()['maintenance_decision'], 'pending')
        # The queue's finally hook must not interpret the exception as a refusal.
        cron_runs.decide_maintenance(execution_id)
        self.confirm(execution_id)
        self.assertEqual(cron_runs.reconcile(now=self.clock), 0)
        self.assertEqual(cron_runs.register_due(self.task_id, '2026-10-14T01:01:00+00:00')['state'], 'skipped')
        with patch.object(ai_settings, 'model_request') as replay:
            db.init_db(); m.init_tables()
            self.assertEqual(cron_runs.reconcile(now='2026-10-14T01:02:00+00:00', recover_decisions=True), 1)
            replay.assert_not_called()
        self.assertEqual(self.round()['maintenance_reason_code'], 'decision_interrupted')
        self.assertIn('未自动重发', self.round()['maintenance_reason'])
        self.assertEqual(db.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'], 1)

    def test_publish_rerun_and_ledger_rollback_at_both_transaction_boundaries(self):
        for point in ('after_rerun_link', 'after_job_finish'):
            with self.subTest(point=point):
                # Independent SQLite fixture for every fault injection.
                helper = CronMaintenanceTests()
                helper.setUp()
                try:
                    execution_id = helper.cron_failure()
                    m.record_failure(execution_id)
                    helper.generate()
                    before = db.fetch_one('SELECT script_path,version FROM tasks WHERE id=?', (helper.task_id,))
                    app_before = db.fetch_one('SELECT script_path FROM rpa_apps WHERE id=?', (helper.app_id,))
                    policy_before = db.fetch_one('SELECT active_version_id,runtime FROM maintenance_policies WHERE task_id=?', (helper.task_id,))
                    method = '_enqueue_repaired_run' if point == 'after_rerun_link' else '_finish'
                    real = getattr(m, method)
                    def fail_after(*args, **kwargs):
                        real(*args, **kwargs)
                        if point == 'after_rerun_link' or args[2] == 'activated':
                            raise RuntimeError('synthetic post-write failure')
                    with patch.object(m, method, side_effect=fail_after):
                        helper.trial()
                    self.assertEqual(db.fetch_one('SELECT script_path,version FROM tasks WHERE id=?', (helper.task_id,)), before)
                    self.assertEqual(db.fetch_one('SELECT script_path FROM rpa_apps WHERE id=?', (helper.app_id,)), app_before)
                    self.assertEqual(db.fetch_one('SELECT active_version_id,runtime FROM maintenance_policies WHERE task_id=?', (helper.task_id,)), policy_before)
                    self.assertIsNone(helper.round()['rerun_execution_id'])
                    self.assertIsNone(helper.job()['rerun_execution_id'])
                    self.assertEqual(helper.job()['status'], 'failed')
                    self.assertEqual(db.fetch_one('SELECT approved FROM task_code_versions WHERE id=?', (helper.job()['candidate_id'],))['approved'], 0)
                    self.assertEqual(db.fetch_one("SELECT COUNT(*) AS n FROM executions WHERE trigger_source='maintenance'")['n'], 0)
                    self.assertEqual(db.fetch_one('SELECT status FROM executions WHERE id=?', (execution_id,))['status'], 'failed')
                finally:
                    helper.doCleanups()

    def test_interrupted_generation_recovery_preserves_job_without_second_model_or_rerun(self):
        execution_id = self.cron_failure()
        m.record_failure(execution_id)
        job_id = self.job()['id']
        claimed = m.claim_generation()
        self.assertEqual(claimed['id'], job_id)
        self.confirm(execution_id)
        with patch.object(ai_settings, 'model_request') as model:
            for _ in range(3):
                db.init_db(); m.init_tables()
                cron_runs.reconcile(now=self.clock, recover_decisions=True)
                self.assertFalse(asyncio.run(m.generate_next()))
            model.assert_not_called()
        self.assertEqual(self.round()['maintenance_job_id'], job_id)
        self.assertEqual(self.round()['maintenance_decision'], 'entered')
        self.assertEqual(self.round()['state'], 'settled')
        self.assertEqual(self.job()['status'], 'interrupted')
        self.assertIsNone(self.job()['rerun_execution_id'])
        self.assertEqual(db.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'], 1)

    def test_interrupted_running_rerun_recovery_cannot_create_a_second_execution(self):
        execution_id = self.cron_failure()
        m.record_failure(execution_id)
        self.confirm(execution_id)
        self.generate(); self.trial()
        rerun_id = self.job()['rerun_execution_id']
        db.execute("UPDATE executions SET status='running',started_at=? WHERE id=?", (self.clock, rerun_id))
        with patch.object(ai_settings, 'model_request') as model:
            for _ in range(3):
                db.init_db(); m.init_tables()
                self.assertEqual(cron_runs.reconcile(now=self.clock, recover_decisions=True), 0)
                self.assertIsNone(self.job()['ended_at'])
            model.assert_not_called()
        self.assertEqual(db.fetch_one('SELECT status FROM executions WHERE id=?', (rerun_id,))['status'], 'failed')
        self.assertEqual(self.round()['rerun_execution_id'], rerun_id)
        self.assertEqual(db.fetch_one("SELECT COUNT(*) AS n FROM executions WHERE trigger_source='maintenance'")['n'], 1)
        self.confirm(rerun_id)
        m.reconcile_reruns()
        self.assertEqual(cron_runs.reconcile(now=self.clock, recover_decisions=True), 1)
        self.assertEqual(self.round()['final_status'], 'failed')

    def run_simulated_rerun(self, *, valid_receipt):
        execution_id = self.cron_failure()
        m.record_failure(execution_id)
        self.confirm(execution_id)
        self.generate(); self.trial()
        rerun_id = self.job()['rerun_execution_id']
        task = {'id': self.task_id, 'maintenance_snapshot': db.fetch_one(
            'SELECT maintenance_snapshot FROM executions WHERE id=?', (rerun_id,))['maintenance_snapshot']}
        work = self.root / 'synthetic-work'
        work.mkdir()
        (work / 'temporary.txt').write_text('synthetic temporary file', encoding='utf-8')
        async def simulated_profile(source, **kwargs):
            self.assertEqual(source, test_maintenance.GOOD)
            self.assertIn('cleanup_receipt', kwargs)
            self.assertTrue(kwargs['cleanup_receipt'].resolve().is_relative_to(self.root))
            if valid_receipt:
                kwargs['cleanup_receipt'].write_text(json.dumps(
                    {'token': kwargs['cleanup_token'], 'complete': True}), encoding='utf-8')
            return {'exit_code': 0, 'log': 'simulated runtime output',
                    'files': {'rows.csv': b'name,value\na,2\nb,4\n'}, 'result': ''}
        with patch.object(maintenance_runtime, 'execute_source', new=AsyncMock(side_effect=simulated_profile)), \
             patch.object(runner, 'WORK_DIR', work), patch.object(runner, '_send_notification', new=AsyncMock()):
            asyncio.run(m.run_managed_execution(rerun_id, task, runner.ExecutionControl()))
        return execution_id, rerun_id, work

    def test_simulated_rerun_valid_receipt_settles_original_failed_and_repair_success_separately(self):
        execution_id, rerun_id, work = self.run_simulated_rerun(valid_receipt=True)
        self.assertEqual(db.fetch_one('SELECT status FROM executions WHERE id=?', (rerun_id,))['status'], 'success')
        self.assertEqual(list(work.iterdir()), [])
        m.record_failure(rerun_id)
        self.assertEqual(cron_runs.reconcile(now=self.clock), 1)
        record = self.round()
        self.assertEqual(record['execution_id'], execution_id)
        self.assertEqual(record['execution_status'], 'failed')
        self.assertEqual(record['rerun_status'], 'success')
        self.assertEqual(record['final_status'], 'success')
        entry = schedules.cron_history(self.task_id, None, {})['items'][0]
        self.assertEqual(entry['execution']['status'], 'failed')
        self.assertEqual(entry['rerun_execution']['status'], 'success')
        self.assertEqual(entry['maintenance_reason_code'], 'queued')

    def test_simulated_rerun_missing_receipt_cannot_release_round_or_clear_workspace(self):
        _, rerun_id, work = self.run_simulated_rerun(valid_receipt=False)
        result = db.fetch_one('SELECT status,result_code FROM executions WHERE id=?', (rerun_id,))
        self.assertEqual(result, {'status': 'failed', 'result_code': 'CLEANUP_UNCONFIRMED'})
        self.assertTrue((work / 'temporary.txt').exists())
        m.record_failure(rerun_id)
        self.assertIsNone(self.job()['ended_at'])
        self.assertEqual(cron_runs.reconcile(now=self.clock, recover_decisions=True), 0)
        self.assertEqual(self.round()['state'], 'active')
        self.assertEqual(db.fetch_one('SELECT COUNT(*) AS n FROM maintenance_jobs')['n'], 1)

    @unittest.skipUnless(os.getenv('SPIDERFLY_TEST_SANDBOX') == '1', 'requires prepared WSL runtime for Cron-owned repair rerun')
    def test_prepared_wsl_worker_executes_cron_owned_repair_once(self):
        execution_id = self.cron_failure()
        m.record_failure(execution_id)
        self.confirm(execution_id)
        self.generate()
        async def drive():
            worker = asyncio.create_task(execution_queue._queue_worker_loop())
            try:
                async with asyncio.timeout(30):
                    while self.round()['state'] != 'settled':
                        await asyncio.sleep(.05)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
        with patch.object(execution_queue, '_host_waiting_reason', return_value=''), patch.object(runner, '_send_notification', new=AsyncMock()):
            asyncio.run(drive())
        record = self.round()
        self.assertEqual(record['execution_id'], execution_id)
        self.assertEqual(record['final_status'], 'success')
        self.assertEqual(record['execution_status'], 'failed')
        self.assertEqual(record['rerun_status'], 'success')
        self.assertEqual(db.fetch_one("SELECT COUNT(*) AS n FROM executions WHERE trigger_source='maintenance'")['n'], 1)
        artifacts = list((self.root / 'executions').rglob('rows.csv'))
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].read_text().splitlines(), ['name,value', 'a,2', 'b,4'])


class CronMaintenanceSchemaTests(unittest.TestCase):
    def test_additive_reason_migration_preserves_transaction_and_existing_round(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        conn.execute("""CREATE TABLE cron_rounds(id INTEGER PRIMARY KEY,task_id INTEGER,task_name TEXT,
            source TEXT,request_key TEXT,request_intent TEXT,requested_by INTEGER,scheduled_for TEXT,
            config TEXT,schedule_revision INTEGER,state TEXT,execution_id INTEGER,maintenance_job_id INTEGER,
            rerun_execution_id INTEGER,maintenance_decision TEXT,reason TEXT,coalesced_from TEXT,
            coalesced_to TEXT,created_at TEXT,ended_at TEXT)""")
        conn.execute("""INSERT INTO cron_rounds(id,task_id,task_name,source,config,schedule_revision,state,
            maintenance_decision,reason,created_at) VALUES(7,3,'legacy','schedule','{}',1,'skipped','none',
            '原计划已跳过','2026-10-14T01:00:00+00:00')""")
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        cron_runs.ensure_schema(conn)
        self.assertTrue(conn.in_transaction)
        row = dict(conn.execute('SELECT * FROM cron_rounds WHERE id=7').fetchone())
        self.assertEqual(row['reason'], '原计划已跳过')
        self.assertEqual(row['maintenance_reason'], '')
        self.assertEqual(row['maintenance_reason_code'], '')
        conn.rollback()
        self.assertNotIn('maintenance_reason', {item['name'] for item in conn.execute('PRAGMA table_info(cron_rounds)')})


if __name__ == '__main__':
    unittest.main()
