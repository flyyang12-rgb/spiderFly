"""HTTP and transaction evidence for inline plans and durable create receipts."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

import httpx

from app import database, environments, main, security
from app.api import workflows as workflow_api
from app.services import creation_requests, workflows
from tests import test_app_api, test_member_management


class InlinePlanTests(unittest.TestCase):
    request = test_member_management.MemberManagementTests.request

    def setUp(self):
        self.helper = test_app_api.ManagedAppApiTests()
        self.item = self.enterContext(self.helper.fixture())
        for name in ('DATA_DIR', 'RPA_APPS_DIR', 'RPA_ENVS_DIR'):
            self.enterContext(patch.object(workflows, name, getattr(database, name)))
        self.enterContext(patch.object(workflows, 'SNAPSHOT_ROOT', database.DATA_DIR / 'workflow_snapshots'))
        self.task = self.helper.create_task(self.item['app_id'], self.item['user'])
        self.token = security.create_session(self.item['user']['id'])[0]

    def upload(self, **changes):
        values = {'name': 'inline-upload', 'request_id': 'inline-upload-request-001',
                  'trigger_type': 'daily', 'trigger_config': '{"time":"09:10"}',
                  'notify_on_success': 'false', 'notify_on_failure': 'false', **changes}

        async def run():
            transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url='http://testserver',
                    cookies={security.SESSION_COOKIE_NAME: self.token}) as client:
                result = await client.post('/api/apps', data=values,
                    files={'script': ('main.py', b"print('synthetic')\n", 'text/x-python')})
                try:
                    body = result.json()
                except ValueError:
                    body = {'detail': result.text}
                return result.status_code, body
        return asyncio.run(run())

    def flow(self, **changes):
        return {'name': 'inline-flow', 'task_ids': [self.task['id']],
                'request_id': 'inline-workflow-request-001',
                'schedule': {'trigger_type': 'weekly', 'trigger_config': {'time': '08:30', 'weekdays': [1, 5]},
                             'schedule_enabled': True}, **changes}

    def test_upload_plan_and_v1_are_committed_together(self):
        code, item = self.upload()
        self.assertEqual(code, 201, item)
        self.assertEqual(item['task']['trigger_type'], 'daily')
        self.assertEqual(item['task']['trigger_config'], {'time': '09:10'})
        self.assertIsNone(item['task']['next_run_at'])
        self.assertEqual(item['task']['first_environment_ready'],0)
        self.assertEqual(item['version_sequence'], 1)
        self.assertEqual(item['environment_status'], 'pending')
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'], 0)

    def test_task_all_modes_and_disabled_plan_do_not_enable_task(self):
        for index, (kind, config) in enumerate([('manual', '{}'), ('daily', '{"time":"10:00"}'),
                ('weekly', '{"time":"10:00","weekdays":[2]}'), ('cron', '{"expression":"0 10 * * *"}')]):
            code, item = self.upload(name=f'upload-{index}', request_id=f'inline-mode-request-{index:03}',
                                     trigger_type=kind, trigger_config=config, enabled='false')
            self.assertEqual(code, 201, item)
            self.assertEqual(item['task']['trigger_type'], kind)
            self.assertFalse(item['task']['enabled'])
            self.assertIsNone(item['task']['next_run_at'])

    def test_upload_retry_is_one_object_one_version_one_audit(self):
        first = self.upload()
        again = self.upload()
        self.assertEqual((first[0], again[0]), (201, 201))
        self.assertEqual(first[1]['task']['id'], again[1]['task']['id'])
        task_id = first[1]['task']['id']
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM task_code_versions WHERE task_id=?', (task_id,))['n'], 1)
        self.assertEqual(database.fetch_one("SELECT COUNT(*) AS n FROM audit_logs WHERE action='create_task' AND target_id=?", (task_id,))['n'], 1)
        self.assertEqual(self.upload(name='different-content')[0], 409)
        code, found = self.request('GET', '/api/creation-requests/task/inline-upload-request-001', token=self.token)
        self.assertEqual(code, 200)
        self.assertEqual(found['id'], task_id)

    def test_concurrent_uploads_with_same_key_create_once(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.upload(), range(2)))
        self.assertEqual([item[0] for item in results], [201, 201], results)
        self.assertEqual(results[0][1]['task']['id'], results[1][1]['task']['id'])

    def test_invalid_upload_plan_leaves_no_new_files_or_receipt(self):
        before = set(database.RPA_APPS_DIR.iterdir())
        code, _ = self.upload(trigger_type='cron', trigger_config='{"expression":"bad"}')
        self.assertEqual(code, 400)
        self.assertEqual(set(database.RPA_APPS_DIR.iterdir()), before)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM creation_requests')['n'], 0)

    def test_receipt_failure_rolls_back_task_audit_and_files(self):
        before = set(database.RPA_APPS_DIR.iterdir())
        with patch.object(creation_requests, 'remember', side_effect=OSError('synthetic receipt failure')):
            self.assertEqual(self.upload()[0], 500)
        self.assertEqual(set(database.RPA_APPS_DIR.iterdir()), before)
        self.assertIsNone(database.fetch_one("SELECT id FROM tasks WHERE name='inline-upload'"))
        self.assertEqual(database.fetch_one("SELECT COUNT(*) AS n FROM audit_logs WHERE action='create_task'")['n'], 0)
        self.assertEqual(self.upload()[0], 201)

    def test_deleted_task_receipt_cannot_recreate_or_rebind_to_reused_app(self):
        code, item = self.upload()
        self.assertEqual(code, 201)
        database.execute("UPDATE rpa_apps SET environment_status='failed' WHERE id=?", (item['id'],))
        environments.delete_task_bundle(item['task']['id'])
        self.assertEqual(self.upload()[0], 409)
        self.assertEqual(self.request('GET', '/api/creation-requests/task/inline-upload-request-001', token=self.token)[0], 409)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM creation_requests')['n'], 1)

    def test_workflow_definition_and_schedule_one_revision_and_receipt(self):
        body = self.flow()
        code, item = self.request('POST', '/api/workflows', body, self.token)
        self.assertEqual(code, 201, item)
        self.assertEqual(item['revision'], 1)
        self.assertTrue(item['schedule_enabled'])
        self.assertEqual(item['trigger_config'], body['schedule']['trigger_config'])
        code, again = self.request('POST', '/api/workflows', body, self.token)
        self.assertEqual(code, 201)
        self.assertEqual(again['id'], item['id'])
        self.assertEqual(self.request('POST', '/api/workflows', self.flow(name='different'), self.token)[0], 409)
        self.assertEqual(database.fetch_one("SELECT COUNT(*) AS n FROM audit_logs WHERE action='create_workflow'")['n'], 1)

    def test_invalid_or_conflicting_flow_plan_rolls_back_entire_create(self):
        bodies = [self.flow(schedule={'trigger_type': 'cron', 'trigger_config': {'expression': 'bad'}, 'schedule_enabled': True}),
                  self.flow(task_ids=[999999])]
        for body in bodies:
            self.assertIn(self.request('POST', '/api/workflows', body, self.token)[0], (400, 409))
            self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM workflows')['n'], 0)
        database.execute("UPDATE tasks SET trigger_type='daily' WHERE id=?", (self.task['id'],))
        code, error = self.request('POST', '/api/workflows', self.flow(), self.token)
        self.assertEqual(code, 409)
        self.assertIn(self.task['name'], error['detail'])
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM creation_requests')['n'], 0)

    def test_concurrent_flow_creates_share_one_receipt(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.request('POST', '/api/workflows', self.flow(), self.token), range(2)))
        self.assertEqual([item[0] for item in results], [201, 201], results)
        self.assertEqual(results[0][1]['id'], results[1][1]['id'])
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM workflows')['n'], 1)

    def test_flow_all_modes_and_disabling_plan_keeps_accepted_round(self):
        for index, (kind, config) in enumerate([('manual', {}), ('daily', {'time': '08:30'}),
                ('weekly', {'time': '08:30', 'weekdays': [2]}), ('cron', {'expression': '0 8 * * *'})]):
            body = self.flow(name=f'flow-mode-{index}', request_id=f'inline-flow-mode-{index:03}',
                schedule={'trigger_type': kind, 'trigger_config': config, 'schedule_enabled': kind != 'manual'})
            code, item = self.request('POST', '/api/workflows', body, self.token)
            self.assertEqual(code, 201, item)
            self.assertEqual(item['trigger_type'], kind)
            self.assertEqual(item['schedule_enabled'], kind != 'manual')
        code, run = self.request('POST', f"/api/workflows/{item['id']}/runs", {'request_id': 'inline-active-round-001'}, self.token)
        self.assertEqual(code, 202, run)
        update = {'name': item['name'], 'task_ids': [self.task['id']], 'revision': item['revision'],
            'schedule': {'trigger_type': 'manual', 'trigger_config': {}, 'schedule_enabled': False}}
        code, closed = self.request('PATCH', f"/api/workflows/{item['id']}", update, self.token)
        self.assertEqual(code, 200, closed)
        self.assertEqual(closed['active_run_id'], run['id'])
        self.assertFalse(closed['schedule_enabled'])
        self.assertEqual(database.fetch_one('SELECT stop_requested FROM workflow_runs WHERE id=?', (run['id'],))['stop_requested'], 0)

    def test_flow_edit_updates_definition_and_plan_atomically_with_revision(self):
        code, item = self.request('POST', '/api/workflows', self.flow(), self.token)
        self.assertEqual(code, 201)
        url = f"/api/workflows/{item['id']}"
        body = {'name': 'updated', 'description': 'changed', 'task_ids': [self.task['id']], 'revision': item['revision'],
                'schedule': {'trigger_type': 'cron', 'trigger_config': {'expression': 'bad'}, 'schedule_enabled': True}}
        self.assertEqual(self.request('PATCH', url, body, self.token)[0], 400)
        stored = self.request('GET', url, token=self.token)[1]
        self.assertEqual((stored['name'], stored['revision'], stored['trigger_type']), ('inline-flow', 1, 'weekly'))
        body['schedule'] = {'trigger_type': 'daily', 'trigger_config': {'time': '12:15'}, 'schedule_enabled': True}
        code, saved = self.request('PATCH', url, body, self.token)
        self.assertEqual(code, 200, saved)
        self.assertEqual((saved['revision'], saved['name'], saved['trigger_type']), (2, 'updated', 'daily'))
        self.assertEqual(self.request('PATCH', url, body, self.token)[0], 409)
        body['revision'] = saved['revision']
        body['schedule'] = {'trigger_type': 'manual', 'trigger_config': {}, 'schedule_enabled': False}
        code, closed = self.request('PATCH', url, body, self.token)
        self.assertEqual(code, 200)
        self.assertFalse(closed['schedule_enabled'])
        self.assertIsNone(closed['next_run_at'])
        self.assertTrue(database.fetch_one('SELECT enabled FROM tasks WHERE id=?', (self.task['id'],))['enabled'])

    def test_definition_only_edit_preserves_disabled_saved_plan(self):
        code, item = self.request('POST', '/api/workflows', self.flow(), self.token)
        url = f"/api/workflows/{item['id']}"
        _, item = self.request('PATCH', url + '/schedule', {'revision': item['revision'], 'trigger_type': 'weekly',
            'trigger_config': {'time': '08:30', 'weekdays': [1, 5]}, 'schedule_enabled': False}, self.token)
        code, saved = self.request('PATCH', url, {'revision': item['revision'], 'name': 'rename', 'task_ids': [self.task['id']]}, self.token)
        self.assertEqual(code, 200)
        self.assertEqual(saved['trigger_config'], item['trigger_config'])
        self.assertEqual(saved['trigger_type'], 'weekly')
        self.assertFalse(saved['schedule_enabled'])

    def test_creation_lookup_requires_admin_and_same_actor_and_keeps_tombstones(self):
        code, item = self.request('POST', '/api/workflows', self.flow(), self.token)
        url = '/api/creation-requests/workflow/inline-workflow-request-001'
        self.assertEqual(self.request('GET', url)[0], 401)
        other = security.create_user('other-admin', '另一管理员', 'admin', 'SyntheticPassword123')
        other_token = security.create_session(other['id'])[0]
        self.assertEqual(self.request('GET', url, token=other_token)[0], 404)
        self.assertEqual(self.request('GET', url, token=self.token)[1]['id'], item['id'])
        self.request('POST', f"/api/workflows/{item['id']}/archive", {}, self.token)
        self.assertEqual(self.request('POST', '/api/workflows', self.flow(), self.token)[0], 409)
        self.assertEqual(self.request('GET', url, token=self.token)[0], 409)
        database.execute("UPDATE users SET role='operator' WHERE id=?", (self.item['user']['id'],))
        self.assertEqual(self.request('GET', url, token=self.token)[0], 403)

    def test_flow_audit_failure_rolls_back_definition_plan_and_receipt(self):
        with patch.object(workflow_api, 'write_audit', side_effect=OSError('synthetic audit failure')):
            with self.assertRaises(OSError):
                self.request('POST', '/api/workflows', self.flow(), self.token)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM workflows')['n'], 0)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM creation_requests')['n'], 0)

    def test_task_edit_audit_failure_rolls_back_name_plan_and_enabled(self):
        before=database.fetch_one('SELECT name,version,enabled,trigger_type,trigger_config,next_run_at FROM tasks WHERE id=?', (self.task['id'],))
        with patch.object(security, 'write_audit', side_effect=OSError('synthetic audit failure')):
            with self.assertRaises(OSError):
                self.request('PATCH', f"/api/tasks/{self.task['id']}",
                    {'version': self.task['version'], 'name': 'should-roll-back', 'enabled': False,
                     'trigger_type': 'daily', 'trigger_config': {'time': '11:00'}}, self.token)
        self.assertEqual(database.fetch_one('SELECT name,version,enabled,trigger_type,trigger_config,next_run_at FROM tasks WHERE id=?', (self.task['id'],)), before)

    def test_workflow_schedule_shortcut_audit_failure_rolls_back_plan_and_revision(self):
        _,item=self.request('POST','/api/workflows',self.flow(),self.token)
        before=database.fetch_one('SELECT * FROM workflows WHERE id=?',(item['id'],))
        with patch.object(workflow_api, 'write_audit', side_effect=OSError('synthetic audit failure')):
            with self.assertRaises(OSError):
                self.request('PATCH',f"/api/workflows/{item['id']}/schedule", {'revision':item['revision'],
                    'trigger_type':'daily','trigger_config':{'time':'18:00'},'schedule_enabled':True},self.token)
        self.assertEqual(database.fetch_one('SELECT * FROM workflows WHERE id=?',(item['id'],)),before)

    def test_legacy_task_create_audit_failure_rolls_back_new_binding(self):
        database.execute('UPDATE tasks SET archived=1 WHERE id=?',(self.task['id'],))
        with patch.object(security, 'write_audit', side_effect=OSError('synthetic audit failure')):
            with self.assertRaises(OSError):
                self.request('POST','/api/tasks',{'name':'legacy-should-roll-back','app_id':self.item['app_id'],
                    'trigger_type':'daily','trigger_config':{'time':'09:00'}},self.token)
        self.assertIsNone(database.fetch_one("SELECT id FROM tasks WHERE name='legacy-should-roll-back'"))
