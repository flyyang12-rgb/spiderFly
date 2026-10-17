"""Signed idle-upgrade reservations use the original host lease transaction."""
from concurrent.futures import ThreadPoolExecutor
import unittest
import uuid

from app import database
from tests import test_hosts


class AgentUpgradeTests(unittest.TestCase):
    for _name in ('setUp','make_task','request','success','new_code','registration','register',
                  'signed_headers','agent','heartbeat','queue','events','detail','host','leased'):
        locals()[_name]=getattr(test_hosts.HostApiTests,_name)

    def pause(self,host,token):
        return self.agent(host,'POST','/api/agent/upgrade-pause',{'token':token})

    def resume(self,host,token):
        return self.agent(host,'POST','/api/agent/upgrade-resume',{'token':token})

    def test_idle_pause_blocks_dispatch_without_changing_user_modes(self):
        host=self.register();token=uuid.uuid4().hex
        before=self.host(host);self.success(self.pause(host,token));run=self.queue(host)
        response=self.heartbeat(host)
        self.assertIsNone(response['run'])
        self.assertEqual(response['host']['state'],'upgrading')
        self.assertTrue(response['host']['upgrade_pending'])
        self.assertNotIn('upgrade_token',response['host'])
        self.assertEqual(self.detail(run['id'])['waiting_reason'],'目标电脑正在升级')
        self.assertEqual(self.host(host)['dispatch_enabled'],before['dispatch_enabled'])
        self.assertEqual(self.host(host)['agent_dispatch_enabled'],before['agent_dispatch_enabled'])
        self.success(self.resume(host,token))
        self.assertEqual(self.heartbeat(host)['run']['id'],run['id'])

    def test_active_lease_refuses_pause_and_does_not_stop_work(self):
        host,run=self.leased()
        self.assertEqual(self.pause(host,uuid.uuid4().hex).status_code,409)
        self.assertEqual(self.host(host)['active_run_id'],run['id'])
        self.assertFalse(self.host(host)['upgrade_pending'])

    def test_pause_and_resume_are_idempotent_and_stale_tokens_cannot_release(self):
        host=self.register();token=uuid.uuid4().hex;other=uuid.uuid4().hex
        self.success(self.pause(host,token));self.success(self.pause(host,token))
        self.assertEqual(self.pause(host,other).status_code,409)
        self.assertEqual(self.resume(host,other).status_code,409)
        self.assertTrue(self.host(host)['upgrade_pending'])
        self.success(self.resume(host,token));self.success(self.resume(host,token))
        self.assertFalse(self.host(host)['upgrade_pending'])

    def test_expired_reservation_does_not_permanently_block_original_queue(self):
        host=self.register();self.success(self.pause(host,uuid.uuid4().hex));run=self.queue(host)
        database.execute('UPDATE agent_hosts SET upgrade_expires_at=1 WHERE id=?',(host,))
        self.assertEqual(self.heartbeat(host)['run']['id'],run['id'])

    def test_frozen_upgrade_blocks_late_claim(self):
        host=self.register();run=self.queue(host)
        self.success(self.pause(host,uuid.uuid4().hex))
        response=self.agent(host,'POST',f'/api/agent/runs/{run["id"]}/claim')
        self.assertEqual(response.status_code,409)
        self.assertIsNone(self.detail(run['id'])['claimed_at'])

    def test_signed_machine_can_only_reserve_its_own_host(self):
        first=self.register();second=self.register();token=uuid.uuid4().hex
        self.success(self.pause(second,token))
        self.assertFalse(self.host(first)['upgrade_pending'])
        body={'token':token,'host_id':first}
        self.assertEqual(self.agent(second,'POST','/api/agent/upgrade-pause',body).status_code,422)

    def test_cookie_auth_and_invalid_payload_do_not_pause_host(self):
        host=self.register()
        self.assertEqual(self.request('POST','/api/agent/upgrade-pause',{'token':uuid.uuid4().hex}).status_code,401)
        self.assertEqual(self.pause(host,'bad').status_code,422)
        self.assertFalse(self.host(host)['upgrade_pending'])

    def test_pause_and_offer_competition_never_grants_both(self):
        host=self.register();run=self.queue(host);token=uuid.uuid4().hex
        with ThreadPoolExecutor(max_workers=2) as pool:
            pause=pool.submit(self.pause,host,token);heartbeat=pool.submit(self.heartbeat,host)
            p=pause.result();h=heartbeat.result()
        if p.status_code==200:
            self.assertIsNone(h['run']);self.assertIsNone(self.host(host)['active_run_id'])
        else:
            self.assertEqual(p.status_code,409);self.assertEqual(h['run']['id'],run['id'])

    def test_revoked_machine_cannot_resume_or_reserve(self):
        host=self.register();self.success(self.request('POST',f'/api/hosts/{host}/revoke'))
        for endpoint in ('upgrade-pause','upgrade-resume'):
            self.assertEqual(self.agent(host,'POST','/api/agent/'+endpoint,{'token':uuid.uuid4().hex}).status_code,403)
