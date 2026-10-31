"""Fixed delay acceptance, completion, restart, transaction and HTTP regression."""
import asyncio
import json
import unittest
from unittest.mock import patch

from app import database, native_process, scheduling
from app.services import execution_queue, host_dispatch, interval_schedules as intervals, workflow_schedules, workflows
from tests import test_inline_plans, test_workflow_schedules, test_hosts


CONFIG = {'value': 30, 'unit': 'minutes'}
FIRST = '2026-10-27T02:00:00+00:00'
FINISH = '2026-10-27T02:08:00+00:00'
NEXT = '2026-10-27T02:38:00+00:00'


class IntervalTaskTests(unittest.TestCase):
    def setUp(self):
        self.h = test_inline_plans.InlinePlanTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        host_dispatch.init_tables()
        self.task_id = self.h.task['id']
        database.execute("UPDATE tasks SET trigger_type='interval',trigger_config=?,next_run_at=? WHERE id=?",
                         (json.dumps(CONFIG), FIRST, self.task_id))

    def scan(self, now=FIRST):
        intervals.scan_tasks(now)

    def next(self):
        return database.fetch_one('SELECT next_run_at FROM tasks WHERE id=?', (self.task_id,))['next_run_at']

    def finish(self, status='success', *, receipt=True):
        run = database.fetch_one('SELECT * FROM executions ORDER BY id DESC LIMIT 1')
        database.execute('UPDATE executions SET status=?,ended_at=? WHERE id=?', (status, FINISH, run['id']))
        if receipt:
            self.receipt(run['id'])
        return run['id']

    def receipt(self, execution_id):
        with patch.object(database, 'utc_now', return_value=FINISH), database.transaction() as conn:
            native_process._begin_settlement_conn(conn, execution_id)
            native_process._update_conn(conn, execution_id, processes_gone=1, workspace_cleanup_confirmed=1, runner_returned=1)

    def test_waits_after_end_and_does_not_repeat_while_queued_or_running(self):
        self.scan('2026-10-27T01:59:59+00:00')
        self.assertEqual(database.fetch_all('SELECT id FROM executions'), [])
        self.scan()
        self.scan('2026-10-27T04:00:00+00:00')
        self.assertIsNone(self.next())
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'], 1)
        run_id = self.finish()
        self.scan('2026-10-27T02:08:01+00:00')
        self.assertEqual(self.next(), NEXT)
        self.scan('2026-10-27T02:37:59+00:00')
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'], 1)
        self.scan(NEXT)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'], 2)
        with database.transaction() as conn:
            self.assertTrue(native_process.coordinated_owned(conn, run_id))

    def test_failed_timeout_and_cancelled_runs_start_a_new_countdown(self):
        for state in ('failed', 'timeout', 'cancelled'):
            with self.subTest(state=state):
                self.scan()
                self.finish(state)
                self.scan(FINISH)
                self.assertEqual(self.next(), NEXT)
                database.execute('DELETE FROM executions')
                database.execute('DELETE FROM execution_settlements')
                database.execute('DELETE FROM interval_schedule_state')
                database.execute('UPDATE tasks SET next_run_at=? WHERE id=?', (FIRST, self.task_id))

    def test_unconfirmed_cleanup_keeps_interval_waiting(self):
        self.scan()
        execution_id = self.finish(receipt=False)
        with database.transaction() as conn:
            native_process._begin_settlement_conn(conn, execution_id)
        self.scan(FINISH)
        self.assertIsNone(self.next())
        with patch.object(database, 'utc_now', return_value=FINISH), database.transaction() as conn:
            native_process._update_conn(conn, execution_id, processes_gone=1, workspace_cleanup_confirmed=1, runner_returned=1)
        self.scan(FINISH)
        self.assertEqual(self.next(), NEXT)

    def test_manual_run_resets_the_countdown(self):
        self.scan('2026-10-27T01:59:00+00:00')
        execution_queue._enqueue_task_sync(self.task_id, 'manual')
        self.scan()
        self.finish()
        self.scan(FINISH)
        self.assertEqual(self.next(), NEXT)

    def test_restarting_preserves_an_overdue_point_and_accepts_only_one(self):
        self.scan('2026-10-27T01:59:00+00:00')
        database.init_db()
        database.init_db()
        self.scan('2026-10-28T02:00:00+00:00')
        self.scan('2026-10-28T02:00:01+00:00')
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'], 1)

    def test_enqueue_failure_rolls_back_the_execution_and_point(self):
        def fail(conn, task_id):
            execution_queue._enqueue_task_in_transaction(task_id, 'schedule', None, conn)
            raise RuntimeError('synthetic commit failure')
        with patch.object(host_dispatch, 'dispatch_scheduled_in_transaction', side_effect=fail), self.assertLogs(intervals.logger, 'ERROR'):
            self.scan()
        self.assertEqual(self.next(), FIRST)
        self.assertEqual(database.fetch_all('SELECT id FROM executions'), [])
        self.scan()
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'], 1)

    def test_disabled_or_unprepared_tasks_do_not_run(self):
        for column in ('enabled', 'first_environment_ready'):
            database.execute(f'UPDATE tasks SET {column}=0 WHERE id=?', (self.task_id,))
            self.scan()
            self.assertEqual(database.fetch_all('SELECT id FROM executions'), [])
            database.execute(f'UPDATE tasks SET {column}=1 WHERE id=?', (self.task_id,))

    def test_editing_before_first_environment_is_ready_keeps_the_gate_closed(self):
        database.execute('UPDATE tasks SET first_environment_ready=0,next_run_at=NULL WHERE id=?', (self.task_id,))
        code, body = self.h.request('PATCH', f'/api/tasks/{self.task_id}', {'description':'unprepared'}, self.h.token)
        self.assertEqual(code, 200, body)
        self.assertIsNone(self.next())

    def test_delete_allows_remote_preparation_but_keeps_local_install_protection(self):
        database.execute('UPDATE tasks SET first_environment_ready=0 WHERE id=?', (self.task_id,))
        database.execute("UPDATE rpa_apps SET environment_status='pending' WHERE id=?", (self.h.item['app_id'],))
        self.assertEqual(self.h.request('DELETE', f'/api/tasks/{self.task_id}', token=self.h.token)[0],409)
        database.execute('UPDATE tasks SET target_host_id=7 WHERE id=?', (self.task_id,))
        code, body = self.h.request('DELETE', f'/api/tasks/{self.task_id}', token=self.h.token)
        self.assertEqual(code,204,body)
        self.assertIsNone(database.fetch_one('SELECT id FROM tasks WHERE id=?',(self.task_id,)))

    def test_delete_still_rejects_remote_active_run_or_actual_install(self):
        database.execute('UPDATE tasks SET target_host_id=7,first_environment_ready=0 WHERE id=?',(self.task_id,))
        database.execute("UPDATE rpa_apps SET environment_status='building' WHERE id=?",(self.h.item['app_id'],))
        self.assertEqual(self.h.request('DELETE',f'/api/tasks/{self.task_id}',token=self.h.token)[0],409)
        database.execute("UPDATE rpa_apps SET environment_status='pending' WHERE id=?",(self.h.item['app_id'],))
        database.execute("INSERT INTO agent_hosts(id,machine_id,name,public_key,created_at,updated_at) VALUES(7,'synthetic','synthetic','unused',?,?)",(FIRST,FIRST))
        database.execute('''INSERT INTO remote_runs(host_id,task_id,task_name,request_id,script,requirements,
            package_hash,timeout_seconds,created_at) VALUES(7,?,'synthetic','delete-busy','print(1)','','hash',60,?)''',(self.task_id,FIRST))
        self.assertEqual(self.h.request('DELETE',f'/api/tasks/{self.task_id}',token=self.h.token)[0],409)

    def test_http_edit_retains_countdown_and_changed_interval_resets_it(self):
        self.scan('2026-10-27T01:59:00+00:00')
        code, value = self.h.request('PATCH', f'/api/tasks/{self.task_id}', {'description': 'changed'}, self.h.token)
        self.assertEqual(code, 200, value)
        self.assertEqual(self.next(), FIRST)
        code, value = self.h.request('PATCH', f'/api/tasks/{self.task_id}',
            {'trigger_config': {'value': 2, 'unit': 'hours'}}, self.h.token)
        self.assertEqual(code, 200, value)
        self.assertNotEqual(self.next(), FIRST)
        saved = self.next()
        self.scan(FINISH)
        self.assertEqual(self.next(), saved)

    def test_http_upload_preview_validation_and_workflow_plan(self):
        code, body = self.h.upload(trigger_type='interval', trigger_config=json.dumps(CONFIG))
        self.assertEqual(code, 201, body)
        self.assertEqual(body['task']['trigger_config'], CONFIG)
        code, body = self.h.request('POST', '/api/schedules/preview-plan',
            {'trigger_type': 'interval', 'trigger_config': CONFIG}, self.h.token)
        self.assertEqual(code, 200, body)
        self.assertEqual(body['times'], [])
        self.assertEqual(body['timing'], 'after_completion')
        database.execute("UPDATE tasks SET trigger_type='manual' WHERE id=?", (self.task_id,))
        code, body = self.h.request('POST', '/api/workflows', self.h.flow(schedule={
            'trigger_type':'interval', 'trigger_config':CONFIG, 'schedule_enabled':True}), self.h.token)
        self.assertEqual(code, 201, body)
        self.assertEqual(body['trigger_config'], CONFIG)
        for value in (True, False, 0, -1, 1.5, '2', None, 100001):
            with self.subTest(value=value):
                code, body = self.h.request('POST', '/api/schedules/preview-plan',
                    {'trigger_type':'interval','trigger_config':{'value':value,'unit':'minutes'}}, self.h.token)
                self.assertEqual(code, 400, body)

    def test_remote_dispatch_waits_for_terminal_report_and_reuses_original_route(self):
        # The real dispatcher and SQLite acceptance run; only host package creation is synthetic.
        database.execute('UPDATE tasks SET target_host_id=7 WHERE id=?', (self.task_id,))
        database.execute("INSERT INTO agent_hosts(id,machine_id,name,public_key,created_at,updated_at) VALUES(7,'synthetic','synthetic','unused',?,?)", (FIRST,FIRST))
        def create(conn, host_id, task_id, request_id, actor, **kwargs):
            cursor = conn.execute('''INSERT INTO remote_runs(host_id,task_id,task_name,requested_by,request_id,
                code_version_id,version_sequence,script,requirements,package_hash,timeout_seconds,created_at)
                VALUES(?,?,?, ?,?,1,1,'print(1)','','hash',60,?)''',
                (host_id,task_id,'synthetic',actor['id'],request_id,FIRST))
            return {'id':cursor.lastrowid}
        with patch.object(host_dispatch, 'create_run_in_transaction', side_effect=create):
            self.scan()
            self.scan('2026-10-27T03:00:00+00:00')
            self.assertIsNone(self.next())
            self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM remote_runs')['n'], 1)
            database.execute("UPDATE remote_runs SET status='success',finished_at=?", (FINISH,))
            self.scan(FINISH)
            self.assertEqual(self.next(), NEXT)
            self.assertEqual(database.fetch_all('SELECT id FROM executions'), [])


class IntervalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.h = test_workflow_schedules.WorkflowScheduleTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)

    def test_whole_workflow_waits_until_round_ends_and_restarts_without_duplicates(self):
        self.h.enable('interval', CONFIG, now='2026-10-14T00:30:00+00:00')
        first = self.h.due()
        self.assertEqual(first['state'], 'active')
        self.assertIsNone(self.h.due('2026-10-14T05:00:00+00:00'))
        self.h.conn.execute("UPDATE workflow_runs SET state='success',ended_at='2026-10-14T01:08:00+00:00' WHERE id=?", (first['run_id'],))
        workflow_schedules.reconcile(self.h.conn)
        point = workflows.get_definition(self.h.conn, self.h.flow['id'])['next_run_at']
        self.assertEqual(point, '2026-10-14T01:38:00+00:00')
        workflow_schedules.reconcile(self.h.conn)
        self.assertEqual(workflows.get_definition(self.h.conn, self.h.flow['id'])['next_run_at'], point)
        result = workflow_schedules.timetable(self.h.conn, '2026-10-14T01:09:00+00:00',
            '2026-10-15T01:09:00+00:00', now='2026-10-14T01:09:00+00:00')
        self.assertEqual(len(result['entries']), 1)
        self.assertEqual(result['entries'][0]['plannedAt'], point)


class IntervalAgentTests(unittest.TestCase):
    def test_real_signed_agent_completion_rearms_only_the_assigned_host(self):
        h = test_hosts.HostApiTests()
        h.setUp()
        self.addCleanup(h.doCleanups)
        host_id = h.register()
        database.execute('UPDATE tasks SET first_environment_ready=0 WHERE id=?', (h.task_id,))
        h.success(h.request('PATCH', f'/api/tasks/{h.task_id}', {
            'trigger_type':'interval','trigger_config':CONFIG,'target_host_id':host_id}))
        database.execute('UPDATE tasks SET next_run_at=? WHERE id=?', (FIRST,h.task_id))
        intervals.scan_tasks(database.utc_now())
        intervals.scan_tasks(database.utc_now())
        runs = database.fetch_all('SELECT * FROM remote_runs')
        self.assertEqual(len(runs),1)
        self.assertEqual(runs[0]['host_id'],host_id)
        self.assertEqual(runs[0]['scheduled_for'],FIRST)
        self.assertEqual(database.fetch_all('SELECT id FROM executions'),[])
        run_id = runs[0]['id']
        self.assertEqual(h.heartbeat(host_id)['run']['id'],run_id)
        h.success(h.agent(host_id,'POST',f'/api/agent/runs/{run_id}/claim'))
        h.success(h.events(host_id,run_id,{'seq':1,'kind':'finished','status':'succeeded','exit_code':0}))
        finished = database.fetch_one('SELECT finished_at FROM remote_runs WHERE id=?',(run_id,))['finished_at']
        intervals.scan_tasks(database.utc_now())
        saved = database.fetch_one('SELECT next_run_at FROM tasks WHERE id=?',(h.task_id,))['next_run_at']
        self.assertEqual(saved,scheduling.compute_next_run('interval',CONFIG,finished))
