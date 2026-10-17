from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException

from app import database, environments, execution_results, native_process, security
from app.api import workflows as workflow_api, executions
from app.services import execution_queue, workflows
from tests import test_app_api, test_member_management


class WorkflowApiTests(unittest.TestCase):
    request = test_member_management.MemberManagementTests.request

    def setUp(self):
        self.helper = test_app_api.ManagedAppApiTests()
        self.item = self.enterContext(self.helper.fixture())
        self.enterContext(patch.object(workflows, 'DATA_DIR', database.DATA_DIR))
        self.enterContext(patch.object(workflows, 'SNAPSHOT_ROOT', database.DATA_DIR / 'workflow_snapshots'))
        self.enterContext(patch.object(workflows, 'RPA_APPS_DIR', database.RPA_APPS_DIR))
        self.enterContext(patch.object(workflows, 'RPA_ENVS_DIR', database.RPA_ENVS_DIR))
        self.task = self.helper.create_task(self.item['app_id'], self.item['user'])
        self.token = security.create_session(self.item['user']['id'])[0]

    def create(self):
        code, item = self.request('POST', '/api/workflows',
            {'name': '接口顺序流程', 'description': '合成测试', 'task_ids': [self.task['id']]}, self.token)
        self.assertEqual(code, 201, item)
        return item

    def start(self, workflow, request_id='workflow-api-request-001'):
        code, run = self.request('POST', f"/api/workflows/{workflow['id']}/runs",
            {'request_id': request_id}, self.token)
        self.assertEqual(code, 202, run)
        return run

    def test_workflow_plan_permissions_revision_and_reverse_task_conflict(self):
        workflow = self.create()
        body = {'revision': workflow['revision'], 'trigger_type': 'daily',
            'trigger_config': {'time': '09:00'}, 'schedule_enabled': True}
        url = f"/api/workflows/{workflow['id']}/schedule"
        self.assertEqual(self.request('PATCH', url, body)[0], 401)
        code, plan = self.request('PATCH', url, body, self.token)
        self.assertEqual(code, 200, plan)
        self.assertTrue(plan['schedule_enabled'])
        self.assertEqual(self.request('PATCH', url, body, self.token)[0], 409)
        code, result = self.request('PATCH', f"/api/tasks/{self.task['id']}",
            {'trigger_type':'daily','trigger_config':{'time':'09:00'}}, self.token)
        self.assertEqual(code, 409, result)
        self.assertIn('统一定时', result['detail'])
        database.execute("UPDATE users SET role='operator' WHERE id=?", (self.item['user']['id'],))
        self.assertEqual(self.request('PATCH', url, {**body,'revision':plan['revision']}, self.token)[0], 403)

    def test_plan_cannot_silently_override_existing_member_schedule(self):
        workflow = self.create()
        code, _ = self.request('PATCH', f"/api/tasks/{self.task['id']}",
            {'trigger_type':'weekly','trigger_config':{'time':'09:00','weekdays':[1]}}, self.token)
        self.assertEqual(code, 200)
        code, result = self.request('PATCH', f"/api/workflows/{workflow['id']}/schedule",
            {'revision':workflow['revision'],'trigger_type':'cron','trigger_config':{'expression':'0 9 * * *'},'schedule_enabled':True}, self.token)
        self.assertEqual(code, 409, result)
        self.assertIn(self.task['name'], result['detail'])
        self.assertEqual(database.fetch_one('SELECT trigger_type FROM tasks WHERE id=?', (self.task['id'],))['trigger_type'], 'weekly')

    def test_plan_preview_and_timetable_validate_range_and_return_object_types(self):
        self.assertEqual(self.request('POST','/api/schedules/preview-plan',
            {'trigger_type':'cron','trigger_config':{'expression':'0 9 1 * MON'}},self.token)[0],400)
        code, preview = self.request('POST','/api/schedules/preview-plan',
            {'trigger_type':'weekly','trigger_config':{'time':'09:00','weekdays':[3]},'after':'2026-10-14T00:59:00+00:00'},self.token)
        self.assertEqual(code,200,preview)
        self.assertEqual(preview['times'][0],'2026-10-14T01:00:00+00:00')
        self.assertEqual(len(preview['times']),5)
        self.assertEqual(self.request('GET','/api/schedules/workflows?start=2026-10-14T00:00:00Z&end=2026-10-23T00:00:00Z',token=self.token)[0],400)
        workflow = self.create()
        self.assertEqual(self.request('GET',f"/api/workflows/{workflow['id']}/triggers",token=self.token)[0],200)

    def test_anonymous_and_member_cannot_mutate_but_member_can_read(self):
        body = {'name': '流程', 'task_ids': [self.task['id']]}
        self.assertEqual(self.request('GET', '/api/workflows')[0], 401)
        self.assertEqual(self.request('POST', '/api/workflows', body)[0], 401)
        workflow = self.create()
        run = self.start(workflow)
        database.execute("UPDATE users SET role='operator' WHERE id=?", (self.item['user']['id'],))
        for method, url, payload in [
            ('POST', '/api/workflows', body),
            ('PATCH', f"/api/workflows/{workflow['id']}", {**body, 'revision': workflow['revision']}),
            ('POST', f"/api/workflows/{workflow['id']}/archive", {}),
            ('POST', f"/api/workflows/{workflow['id']}/runs", {'request_id': 'member-request-001'}),
            ('POST', f"/api/workflow-runs/{run['id']}/stop", {}),
        ]:
            self.assertEqual(self.request(method, url, payload, self.token)[0], 403)
        self.assertEqual(self.request('GET', '/api/workflows', token=self.token)[0], 200)
        self.assertEqual(self.request('GET', f"/api/workflow-runs/{run['id']}", token=self.token)[0], 200)

    def test_validation_revision_archive_and_audit(self):
        for task_ids in ([], [self.task['id'], self.task['id']], [-1]):
            self.assertEqual(self.request('POST', '/api/workflows', {'name': '流程', 'task_ids': task_ids}, self.token)[0], 422)
        workflow = self.create()
        body = {'name': '已编辑', 'description': '', 'task_ids': [self.task['id']], 'revision': workflow['revision']}
        self.assertEqual(self.request('PATCH', f"/api/workflows/{workflow['id']}", body, self.token)[0], 200)
        self.assertEqual(self.request('PATCH', f"/api/workflows/{workflow['id']}", body, self.token)[0], 409)
        run = self.start(workflow)
        self.assertEqual(self.request('POST', f"/api/workflows/{workflow['id']}/archive", {}, self.token)[0], 409)
        code, stopped = self.request('POST', f"/api/workflow-runs/{run['id']}/stop", {}, self.token)
        self.assertEqual(code, 202)
        self.assertEqual(stopped['status'], 'cancelled')
        self.assertEqual(self.request('POST', f"/api/workflows/{workflow['id']}/archive", {}, self.token)[0], 200)
        actions = {item['action'] for item in database.fetch_all('SELECT action FROM audit_logs')}
        self.assertTrue({'create_workflow', 'update_workflow', 'start_workflow', 'stop_workflow', 'archive_workflow'} <= actions)

    def test_network_retry_keeps_original_run_after_stop_and_archive(self):
        workflow = self.create()
        first = self.start(workflow)
        again = self.start(workflow)
        self.assertEqual(first['id'], again['id'])
        self.request('POST', f"/api/workflow-runs/{first['id']}/stop", {}, self.token)
        self.request('POST', f"/api/workflows/{workflow['id']}/archive", {}, self.token)
        retry = self.start(workflow)
        self.assertEqual(retry['id'], first['id'])
        self.assertEqual(retry['status'], 'cancelled')
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM workflow_runs')['n'], 1)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM executions')['n'], 1)

    def test_public_history_has_no_source_private_files_and_validates_filters(self):
        workflow = self.create()
        run = self.start(workflow)
        code, result = self.request('GET', f"/api/workflow-runs?workflow_id={workflow['id']}&page=1&page_size=1", token=self.token)
        self.assertEqual(code, 200)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['id'], run['id'])
        text = str(run)
        for private in ('snapshot_storage_dir', 'python_path', 'script_path', 'template_path', "print('ok')"):
            self.assertNotIn(private, text)
        self.assertEqual(self.request('GET', '/api/workflow-runs?status=bogus', token=self.token)[0], 400)
        self.assertEqual(self.request('GET', '/api/workflow-runs?date_from=2026-10-14&date_to=2026-10-13', token=self.token)[0], 400)

    def test_independent_manual_and_schedule_cannot_overlap_current_step(self):
        workflow = self.create()
        run = self.start(workflow)
        for source in ('manual', 'schedule'):
            with self.assertRaises(HTTPException) as caught:
                execution_queue._enqueue_task_sync(self.task['id'], source, self.item['user']['id'])
            self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.request('POST', f"/api/executions/{run['steps'][0]['execution_id']}/cancel", {}, self.token)[0], 200)
        with database.transaction() as conn:
            ended = workflows.get_run(conn, run['id'])
        self.assertEqual(ended['status'], 'cancelled')

    def test_task_deletion_cancels_unstarted_flow_preserves_six_field_summary(self):
        workflow = self.create()
        run = self.start(workflow)
        self.assertEqual(self.request('DELETE', f"/api/tasks/{self.task['id']}", token=self.token), (204, None))
        code, history = self.request('GET', f"/api/workflow-runs/{run['id']}", token=self.token)
        self.assertEqual(code, 200)
        step = history['steps'][0]
        for field in ('position', 'task_name', 'task_version', 'started_at', 'ended_at', 'status', 'error_summary'):
            self.assertIn(field, step)
        self.assertFalse(step['execution_available'])
        self.assertEqual(self.request('POST', f"/api/workflows/{workflow['id']}/runs", {'request_id': 'deleted-member-run-001'}, self.token)[0], 409)

    def test_http_task_delete_removes_idle_bundle_and_history_and_audits_once(self):
        now = database.utc_now()
        execution_id = database.execute(
            "INSERT INTO executions(task_id,status,exit_code,created_at,ended_at) VALUES(?,'success',0,?,?)",
            (self.task['id'], now, now))
        workspace = execution_results.create_execution_workspace(execution_id)
        (workspace.artifacts_dir / 'synthetic.txt').write_text('synthetic', encoding='utf-8')
        self.assertEqual(self.request('DELETE', f"/api/tasks/{self.task['id']}", token=self.token), (204, None))
        for table, identifier in [('tasks', self.task['id']), ('rpa_apps', self.item['app_id']), ('executions', execution_id)]:
            self.assertIsNone(database.fetch_one(f'SELECT id FROM {table} WHERE id=?', (identifier,)))
        for path in (self.item['app_dir'], self.item['env_dir'], workspace.root):
            self.assertFalse(path.exists(), str(path))
        audit = database.fetch_all("SELECT target_id FROM audit_logs WHERE action='delete_task'")
        self.assertEqual([str(row['target_id']) for row in audit], [str(self.task['id'])])

    def test_http_task_delete_running_conflict_preserves_bundle_without_success_audit(self):
        database.execute("INSERT INTO executions(task_id,status,created_at) VALUES(?,'running',?)",
                         (self.task['id'], database.utc_now()))
        code, result = self.request('DELETE', f"/api/tasks/{self.task['id']}", token=self.token)
        self.assertEqual(code, 409, result)
        self.assertIn('正在运行', result['detail'])
        self.assertIsNotNone(database.fetch_one('SELECT id FROM tasks WHERE id=?', (self.task['id'],)))
        self.assertTrue(self.item['app_dir'].is_dir())
        self.assertTrue(self.item['env_dir'].is_dir())
        self.assertEqual(database.fetch_all("SELECT id FROM audit_logs WHERE action='delete_task'"), [])

    def test_http_task_delete_checks_auth_and_missing_task(self):
        url = f"/api/tasks/{self.task['id']}"
        self.assertEqual(self.request('DELETE', url)[0], 401)
        self.assertEqual(self.request('DELETE', '/api/tasks/999999', token=self.token)[0], 404)
        database.execute("UPDATE users SET role='operator' WHERE id=?", (self.item['user']['id'],))
        self.assertEqual(self.request('DELETE', url, token=self.token)[0], 403)
        self.assertIsNotNone(database.fetch_one('SELECT id FROM tasks WHERE id=?', (self.task['id'],)))
        self.assertTrue(self.item['app_dir'].is_dir())
        self.assertEqual(database.fetch_all("SELECT id FROM audit_logs WHERE action='delete_task'"), [])

    def test_reconcile_failure_blocks_claim_and_maintenance_trials(self):
        with (
            patch.object(native_process, 'recover_settlements', return_value=[]),
            patch.object(execution_queue.maintenance, 'run_next_trial', new=AsyncMock()) as trial,
            patch.object(execution_queue, '_claim_next_execution') as claim,
            patch.object(execution_queue, '_next_pending_execution_id', return_value=None),
            patch.object(workflows, 'reconcile', side_effect=RuntimeError('synthetic workflow recovery error')),
            patch.object(execution_queue.asyncio, 'sleep', new=AsyncMock(side_effect=asyncio.CancelledError)),
            self.assertLogs(execution_queue.logger, level='ERROR'),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(execution_queue._queue_worker_loop())
        claim.assert_not_called()
        trial.assert_not_called()
