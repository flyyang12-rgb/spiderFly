"""Signed reapplication preserves identity/history and always requires approval."""
import base64
import json
import time
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from app import database
from tests import test_hosts


class HostReenrollmentTests(unittest.TestCase):
    def setUp(self):
        self.h = test_hosts.HostApiTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)

    def payload(self, host_id, code=None):
        host = database.fetch_one('SELECT * FROM agent_hosts WHERE id=?', (host_id,))
        value = {'enrollment_code': code or self.h.new_code(), 'machine_id': host['machine_id'],
                 'name': 'bot2-renamed', 'public_key': host['public_key'], 'agent_version': '0.5.1', 'protocol_version': 1}
        self.sign(value, self.h.keys[host_id])
        return value

    def sign(self, value, key):
        fields = {k:v for k,v in value.items() if k != 'reenrollment_signature'}
        message = json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        value['reenrollment_signature'] = base64.b64encode(key.sign(message)).decode()

    def apply(self, payload):
        return self.h.request('POST','/api/agent/register',payload,user=None,origin=False)

    def test_reapply_existing_states_and_retry_keeps_one_pending_record(self):
        for state in ('approved','pending','rejected','revoked'):
            host_id = self.h.register()
            database.execute('UPDATE agent_hosts SET approval_status=? WHERE id=?', (state,host_id))
            payload = self.payload(host_id)
            result = self.h.success(self.apply(payload))
            self.assertEqual((result['host_id'],result['approval_status']), (host_id,'pending'))
            stored = database.fetch_one('SELECT * FROM agent_hosts WHERE id=?',(host_id,))
            self.assertEqual(stored['name'],'bot2-renamed')
            self.assertEqual(stored['dispatch_enabled'],0)
            before = database.fetch_one("SELECT COUNT(*) n FROM audit_logs WHERE action='agent_register'")['n']
            self.assertEqual(self.h.success(self.apply(payload))['host_id'],host_id)
            self.assertEqual(database.fetch_one("SELECT COUNT(*) n FROM audit_logs WHERE action='agent_register'")['n'],before)

    def test_deleted_registration_creates_new_pending_identity_without_restoring_tombstone(self):
        host_id = self.h.register()
        payload = self.payload(host_id)
        self.h.success(self.h.request('POST',f'/api/hosts/{host_id}/revoke'))
        self.h.success(self.h.request('DELETE',f'/api/hosts/{host_id}'))
        result = self.h.success(self.apply(payload))
        self.assertNotEqual(result['host_id'],host_id)
        self.assertEqual(result['approval_status'],'pending')
        self.assertTrue(database.fetch_one('SELECT deleted_at FROM agent_hosts WHERE id=?',(host_id,))['deleted_at'])

    def test_new_code_alone_cannot_replace_existing_machine_or_change_approval(self):
        host_id = self.h.register()
        value = self.payload(host_id)
        value.pop('reenrollment_signature')
        self.assertEqual(self.apply(value).status_code,409)
        self.sign(value,Ed25519PrivateKey.generate())
        self.assertEqual(self.apply(value).status_code,401)
        self.assertEqual(database.fetch_one('SELECT approval_status FROM agent_hosts WHERE id=?',(host_id,))['approval_status'],'approved')

    def test_signature_covers_name_and_new_code_and_failed_request_does_not_consume_code(self):
        host_id = self.h.register()
        value = self.payload(host_id)
        value['name'] = 'tampered'
        self.assertEqual(self.apply(value).status_code,401)
        self.sign(value,self.h.keys[host_id])
        self.assertEqual(self.apply(value).status_code,201)

    def test_active_work_or_upgrade_blocks_reapplication(self):
        host_id = self.h.register()
        self.h.queue(host_id)
        value = self.payload(host_id)
        self.assertEqual(self.apply(value).status_code,409)
        database.execute("UPDATE remote_runs SET status='cancelled' WHERE host_id=?",(host_id,))
        database.execute('UPDATE agent_hosts SET upgrade_token=?,upgrade_expires_at=? WHERE id=?',('synthetic',time.time()+100,host_id))
        self.assertEqual(self.apply(value).status_code,409)

    def test_status_is_signed_does_not_offer_tasks_and_can_report_revocation(self):
        host_id = self.h.register()
        self.h.queue(host_id)
        before = database.fetch_one('SELECT active_run_id,last_seen_at FROM agent_hosts WHERE id=?',(host_id,))
        result = self.h.success(self.h.agent(host_id,'GET','/api/agent/registration'))
        self.assertEqual(result['approval_status'],'approved')
        self.assertFalse(result['can_reapply'])
        self.assertEqual(database.fetch_one('SELECT active_run_id,last_seen_at FROM agent_hosts WHERE id=?',(host_id,)),before)
        database.execute("UPDATE remote_runs SET status='cancelled' WHERE host_id=?",(host_id,))
        self.h.success(self.h.request('POST',f'/api/hosts/{host_id}/revoke'))
        self.assertEqual(self.h.success(self.h.agent(host_id,'GET','/api/agent/registration'))['approval_status'],'revoked')
        self.assertEqual(self.h.agent(host_id,'POST','/api/agent/heartbeat',{}).status_code,403)
        self.assertEqual(self.h.request('GET','/api/agent/registration',user=None,origin=False).status_code,401)
        self.assertEqual(self.h.agent(host_id,'GET','/api/agent/registration',key=Ed25519PrivateKey.generate()).status_code,401)
