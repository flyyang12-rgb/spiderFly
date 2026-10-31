"""Revoked registration deletion: authorization, history, identity and races."""
from concurrent.futures import ThreadPoolExecutor
import json
import time
import unittest
from unittest.mock import patch

from app import database
from app.services import host_dispatch
from tests import test_hosts


class HostDeletionTests(unittest.TestCase):
    def setUp(self):
        self.h = test_hosts.HostApiTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)

    def revoked(self):
        host_id = self.h.register()
        self.h.success(self.h.request('POST', f'/api/hosts/{host_id}/revoke'))
        return host_id

    def delete(self, host_id, **kwargs):
        return self.h.request('DELETE', f'/api/hosts/{host_id}', **kwargs)

    def test_only_revoked_hosts_can_be_deleted(self):
        for state in ('pending', 'approved', 'rejected'):
            host_id = self.h.register(approved=False)
            database.execute('UPDATE agent_hosts SET approval_status=? WHERE id=?', (state, host_id))
            response = self.delete(host_id)
            self.assertEqual(response.status_code, 409)
            self.assertIsNone(database.fetch_one('SELECT deleted_at FROM agent_hosts WHERE id=?', (host_id,))['deleted_at'])
        self.assertEqual(self.delete(999999).status_code, 404)

    def test_delete_requires_admin_same_origin_and_json(self):
        host_id = self.revoked()
        for options, status in [({'user': None}, 401), ({'user': 'member'}, 403),
                                ({'origin': False}, 403), ({'headers': {'Origin': 'https://other.test'}}, 403),
                                ({'headers': {'Content-Type': 'text/plain'}}, 415)]:
            with self.subTest(options=options):
                self.assertEqual(self.delete(host_id, **options).status_code, status)
        self.assertEqual(self.delete(host_id).status_code, 200)

    def test_delete_removes_registration_identity_and_cannot_be_restored(self):
        host_id = self.revoked()
        original = database.fetch_one('SELECT * FROM agent_hosts WHERE id=?', (host_id,))
        self.assertGreater(database.fetch_one('SELECT COUNT(*) AS n FROM agent_nonces WHERE host_id=?', (host_id,))['n'], 0)
        self.assertEqual(self.h.success(self.delete(host_id)), {'deleted': True, 'id': host_id})
        tombstone = database.fetch_one('SELECT * FROM agent_hosts WHERE id=?', (host_id,))
        self.assertTrue(tombstone['deleted_at'])
        self.assertEqual(tombstone['public_key'], '')
        self.assertNotEqual(tombstone['machine_id'], original['machine_id'])
        for table, field in [('agent_nonces', 'host_id'), ('agent_enrollment_codes', 'consumed_by')]:
            self.assertEqual(database.fetch_one(f'SELECT COUNT(*) AS n FROM {table} WHERE {field}=?', (host_id,))['n'], 0)
        host_dispatch.init_tables()
        self.assertNotIn(host_id, [row['id'] for row in self.h.success(self.h.request('GET', '/api/hosts'))])
        self.assertEqual(self.h.agent(host_id, 'POST', '/api/agent/heartbeat', {
            'protocol_version': 1, 'agent_version': '0.3.0', 'dispatch_enabled': True, 'interactive_session': True,
        }).status_code, 404)
        self.assertEqual(self.h.request('POST', f'/api/hosts/{host_id}/approve').status_code, 404)
        self.assertEqual(self.h.request('PATCH', f'/api/hosts/{host_id}/mode', {'dispatch_enabled': True}).status_code, 404)
        self.assertEqual(self.h.request('POST', f'/api/hosts/{host_id}/runs', {'task_id': self.h.task_id, 'request_id': 'deleted-host-new-run'}).status_code, 404)

    def test_bound_tasks_block_deletion_without_retargeting_even_when_disabled(self):
        host_id = self.revoked()
        database.execute('UPDATE tasks SET target_host_id=?,enabled=0 WHERE id=?', (host_id, self.h.task_id))
        original = database.fetch_one('SELECT * FROM tasks WHERE id=?', (self.h.task_id,))
        response = self.delete(host_id)
        self.assertEqual(response.status_code, 409)
        self.assertIn('1 个任务', response.json()['detail'])
        self.assertEqual(database.fetch_one('SELECT * FROM tasks WHERE id=?', (self.h.task_id,)), original)
        database.execute('UPDATE tasks SET archived=1 WHERE id=?', (self.h.task_id,))
        self.h.success(self.delete(host_id))
        self.assertEqual(database.fetch_one('SELECT target_host_id FROM tasks WHERE id=?', (self.h.task_id,))['target_host_id'], host_id)

    def test_unfinished_runs_and_stale_active_pointer_block_deletion(self):
        host_id = self.h.register(enabled=False)
        run = self.h.queue(host_id)
        database.execute("UPDATE agent_hosts SET approval_status='revoked' WHERE id=?", (host_id,))
        for status in ('queued', 'preparing', 'running', 'stopping', 'uncertain'):
            database.execute('UPDATE remote_runs SET status=? WHERE id=?', (status, run['id']))
            self.assertEqual(self.delete(host_id).status_code, 409, status)
        database.execute("UPDATE remote_runs SET status='cancelled' WHERE id=?", (run['id'],))
        database.execute('UPDATE agent_hosts SET active_run_id=? WHERE id=?', (run['id'], host_id))
        self.assertEqual(self.delete(host_id).status_code, 409)

    def test_active_upgrade_ledger_and_local_upgrade_lease_block_deletion(self):
        host_id = self.revoked()
        database.execute('UPDATE agent_hosts SET upgrade_token=?,upgrade_expires_at=? WHERE id=?', ('a'*32, time.time()+600, host_id))
        self.assertEqual(self.delete(host_id).status_code, 409)
        database.execute("UPDATE agent_hosts SET upgrade_token='',upgrade_expires_at=0 WHERE id=?", (host_id,))
        database.execute('''INSERT INTO agent_update_jobs(id,host_id,request_id,from_version,target_version,
            token,package_sha256,package,offer_json,signature,status,created_at,updated_at,expires_at)
            VALUES ('synthetic-job',?,'synthetic-request','0.5.0','0.5.1','synthetic-token','hash',X'00','{}','','queued',?,0,?)''',
            (host_id, database.utc_now(), time.time()+600))
        for status in ('queued', 'downloading', 'installing', 'awaiting_reconnect', 'uncertain'):
            database.execute('UPDATE agent_update_jobs SET status=?', (status,))
            self.assertEqual(self.delete(host_id).status_code, 409, status)
        database.execute("UPDATE agent_update_jobs SET status='failed'")
        self.h.success(self.delete(host_id))
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM agent_update_jobs')['n'], 1)

    def test_history_logs_artifact_environment_and_foreign_keys_survive(self):
        host_id, run = self.h.leased()
        self.h.success(self.h.agent(host_id, 'POST', f"/api/agent/runs/{run['id']}/artifacts/result.txt", raw=b'synthetic result'))
        self.h.success(self.h.events(host_id, run['id'], {'seq': 1, 'kind': 'stdout', 'text': 'synthetic log'},
            {'seq': 2, 'kind': 'finished', 'status': 'succeeded', 'exit_code': 0}))
        database.execute('INSERT INTO remote_environment_records VALUES(?,?,?)', (run['id'], json.dumps({'status': 'ready'}), database.utc_now()))
        before = self.h.detail(run['id'])
        self.h.success(self.h.request('POST', f'/api/hosts/{host_id}/revoke'))
        self.h.success(self.delete(host_id))
        self.assertEqual(self.h.detail(run['id']), before)
        self.assertEqual(self.h.request('GET', before['artifacts'][0]['download_url']).content, b'synthetic result')
        self.assertEqual(self.h.success(self.h.request('GET', '/api/remote-runs'))[0]['id'], run['id'])
        with database.transaction() as conn:
            self.assertEqual(list(conn.execute('PRAGMA foreign_key_check')), [])

    def test_old_code_cannot_be_reused_but_new_enrollment_requires_approval(self):
        payload, key = self.h.registration()
        host_id = self.h.success(self.h.request('POST', '/api/agent/register', payload, user=None))['host_id']
        self.h.keys[host_id] = key
        self.h.success(self.h.request('POST', f'/api/hosts/{host_id}/revoke'))
        self.h.success(self.delete(host_id))
        self.assertEqual(self.h.request('POST', '/api/agent/register', payload, user=None).status_code, 400)
        new = self.h.success(self.h.request('POST', '/api/agent/register', {**payload, 'enrollment_code': self.h.new_code()}, user=None))
        self.assertNotEqual(new['host_id'], host_id)
        self.assertEqual(new['approval_status'], 'pending')

    def test_duplicate_requests_are_idempotent_and_audited_once(self):
        host_id = self.revoked()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.delete(host_id), range(2)))
        self.assertEqual([r.status_code for r in responses], [200, 200])
        self.assertEqual(database.fetch_one("SELECT COUNT(*) AS n FROM audit_logs WHERE action='agent_delete' AND target_id=?", (host_id,))['n'], 1)

    def test_audit_failure_rolls_back_identity_and_code_changes(self):
        host_id = self.revoked()
        before = database.fetch_one('SELECT * FROM agent_hosts WHERE id=?', (host_id,))
        actor = database.fetch_one("SELECT * FROM users WHERE username='admin'")
        with patch.object(host_dispatch, '_audit', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                host_dispatch.delete_host(host_id, actor)
        self.assertEqual(database.fetch_one('SELECT * FROM agent_hosts WHERE id=?', (host_id,)), before)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM agent_enrollment_codes WHERE consumed_by=?', (host_id,))['n'], 1)
