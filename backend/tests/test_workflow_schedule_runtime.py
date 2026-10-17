"""Scheduled workflow acceptance followed by real Windows Job executions.

The due clock is controlled; this is backend integration, not browser evidence.
"""
import os
from datetime import datetime, timedelta, timezone
import unittest

from app import database
from app.services import workflows, workflow_schedules as plans
from tests import test_workflow_runtime


@unittest.skipUnless(os.name == 'nt', 'Windows Job Object requires Windows')
class ScheduledWorkflowRunnerTests(unittest.TestCase):
    def setUp(self):
        self.h = test_workflow_runtime.WorkflowCoreRunnerTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.now = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    def enable_and_scan(self, kind='cron', config=None):
        with database.transaction() as conn:
            definition = workflows.get_definition(conn, self.h.workflow['id'])
            plans.update_plan(conn, definition['id'], kind, config or {'expression':'* * * * *'},
                True, definition['revision'], self.h.user['id'], self.now-timedelta(minutes=1))
        plans.scan_due(self.now)
        record = database.fetch_one('SELECT * FROM workflow_triggers WHERE workflow_id=?', (self.h.workflow['id'],))
        self.assertIsNotNone(record['run_id'])
        return record

    def test_cron_scan_accepts_all_three_steps_and_real_runner_finishes_whole_round(self):
        order = self.h.root/'synthetic-order.txt'
        prefix = 'from pathlib import Path\np=Path(' + repr(str(order)) + ')\n'
        self.h.path.write_text(prefix+'p.write_text("A")\n', encoding='utf-8')
        self.h.second_path.write_text(prefix+'assert p.read_text()=="A"\np.write_text("AB")\n', encoding='utf-8')
        now = database.utc_now()
        app_id = database.execute("INSERT INTO rpa_apps(name,environment_status,created_at,updated_at) VALUES('第三步','ready',?,?)", (now,now))
        third = self.h.root/'apps'/str(app_id)/'main.py'
        third.parent.mkdir()
        third.write_text(prefix+'assert p.read_text()=="AB"\np.write_text("ABC")\n', encoding='utf-8')
        database.execute('UPDATE rpa_apps SET script_path=? WHERE id=?', (str(third),app_id))
        task_id = database.execute("INSERT INTO tasks(name,app_id,script_path,created_by,notify_on_success,notify_on_failure,created_at,updated_at) VALUES('第三步',?,?,?,0,0,?,?)", (app_id,str(third),self.h.user['id'],now,now))
        with database.transaction() as conn:
            definition = workflows.get_definition(conn,self.h.workflow['id'])
            workflows.update_definition(conn,definition['id'],definition['name'],'',[self.h.task_id,self.h.second_task,task_id],self.h.user['id'],expected_revision=definition['revision'])
        record = self.enable_and_scan()
        self.assertEqual(len(self.h.get_run(record['run_id'])['steps']),3)
        third.write_text('raise RuntimeError("must not use new source")\n',encoding='utf-8')
        ids = [self.h.execute_current(record['run_id']) for _ in range(3)]
        plans.reconcile()
        self.assertEqual(order.read_text(),'ABC')
        self.assertEqual(len(set(ids)),3)
        self.assertEqual(self.h.get_run(record['run_id'])['status'],'success')
        saved = database.fetch_one('SELECT * FROM workflow_triggers WHERE id=?',(record['id'],))
        self.assertEqual((saved['state'],saved['final_status']),('settled','success'))
        self.assertIsNotNone(saved['enqueued_at'])
        self.h.capture.assert_not_called()
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM maintenance_jobs')['n'],0)

    def test_daily_plan_real_failure_skips_second_step_without_maintenance(self):
        self.h.path.write_text('raise RuntimeError("scheduled synthetic failure")\n',encoding='utf-8')
        record = self.enable_and_scan('daily',{'time':self.now.astimezone(plans.CHINA_TZ).strftime('%H:%M')})
        self.h.execute_current(record['run_id'])
        plans.reconcile()
        run = self.h.get_run(record['run_id'])
        self.assertEqual([step['status'] for step in run['steps']],['failed','skipped'])
        self.assertIsNone(run['steps'][1]['execution_id'])
        saved = database.fetch_one('SELECT * FROM workflow_triggers WHERE id=?',(record['id'],))
        self.assertEqual(saved['final_status'],'failed')
        self.assertIn('scheduled synthetic failure',saved['reason'])
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM maintenance_jobs')['n'],0)
