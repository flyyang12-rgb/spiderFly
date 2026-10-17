"""Real signed HTTP/SQLite update lifecycle; never runs a production Agent."""
import base64
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import time
import unittest
from unittest.mock import patch
import uuid
import zipfile

from app import database
from app.api import hosts as api
from app.services import agent_updates as updates, host_dispatch as hosts
from tests import test_hosts


class RemoteAgentUpdateTests(unittest.TestCase):
    for _name in ('setUp','make_task','request','success','new_code','registration','register',
                  'signed_headers','agent','heartbeat','queue','events','detail','host','leased','make_agent_sources'):
        if _name != 'setUp':locals()[_name]=getattr(test_hosts.HostApiTests,_name)

    def setUp(self):
        test_hosts.HostApiTests.setUp(self)
        database.execute("UPDATE users SET role='super_admin' WHERE username='admin'")
        source, _ = self.make_agent_sources()
        (source/'spiderfly_agent/__init__.py').write_text('__version__ = "0.5.1"\n')
        self.stack.enter_context(patch.object(api,'AGENT_ROOT',source))
        self.stack.enter_context(patch.object(hosts,'CURRENT_AGENT_VERSION','0.5.1'))

    def ready(self,**kw):
        host=self.register(**kw)
        self.heartbeat(host,agent_version='0.5.0',remote_upgrade_ready=True)
        return host

    def create(self,host,key=None,**kw):
        return self.request('POST',f'/api/hosts/{host}/upgrade',{'request_id':key or uuid.uuid4().hex},**kw)

    def offer(self,host):
        return self.heartbeat(host,agent_version='0.5.0',remote_upgrade_ready=True)['update']

    def report(self,host,offer,phase='result',success=True,version='0.5.1',message=''):
        d=offer['descriptor']
        return self.agent(host,'POST',f"/api/agent/updates/{d['id']}/report",
                          {'token':d['token'],'phase':phase,'success':success,'agent_version':version,'message':message})

    def installing(self,host,offer):
        return self.report(host,offer,'installing',False,'0.5.0')

    def package(self,host,offer):
        return self.agent(host,'GET',f"/api/agent/updates/{offer['descriptor']['id']}/package")

    def test_create_reserves_host_audits_and_returns_no_private_token_or_package(self):
        host=self.ready();job=self.success(self.create(host));self.queue(host)
        response=self.heartbeat(host,agent_version='0.5.0',remote_upgrade_ready=True)
        self.assertIsNone(response['run']);self.assertEqual(response['host']['state'],'upgrading')
        self.assertEqual(response['update']['descriptor']['id'],job['id'])
        self.assertNotIn('token',job);self.assertNotIn('package',job)
        self.assertNotIn('remote_upgrade_id',response['host'])
        self.assertTrue(database.fetch_one("SELECT 1 FROM audit_logs WHERE action='agent_upgrade'"))

    def test_package_frozen_signed_and_source_changes_do_not_change_download(self):
        host=self.ready();self.success(self.create(host));offer=self.offer(host);d=offer['descriptor']
        key=updates.signing_key();key.public_key().verify(base64.b64decode(offer['signature']),updates.canonical(d))
        first=self.package(host,offer);self.assertEqual(first.status_code,200)
        (api.AGENT_ROOT/'requirements.txt').write_text('later change')
        second=self.package(host,offer);self.assertEqual(first.content,second.content)
        self.assertEqual(hashlib.sha256(first.content).hexdigest(),d['sha256'])
        with zipfile.ZipFile(io.BytesIO(first.content)) as z:
            self.assertEqual(json.loads(z.read('SpiderFlyAgent/controller-trust.json')),updates.trust(key))

    def test_result_does_not_release_until_matching_new_heartbeat(self):
        host=self.ready();self.success(self.create(host));offer=self.offer(host);run=self.queue(host)
        self.success(self.installing(host,offer));self.success(self.report(host,offer))
        self.assertEqual(self.host(host)['agent_update']['status'],'awaiting_reconnect')
        self.assertIsNone(self.heartbeat(host,agent_version='0.5.0',remote_upgrade_ready=True)['run'])
        response=self.heartbeat(host,agent_version='0.5.1',remote_upgrade_ready=True)
        self.assertEqual(response['host']['agent_update']['status'],'succeeded')
        self.assertEqual(response['run']['id'],run['id'])
        self.assertEqual(self.host(host)['agent_version'],'0.5.1')

    def test_failed_upgrade_requires_original_version_reconnect_and_preserves_modes(self):
        host=self.ready(enabled=False);self.heartbeat(host,agent_version='0.5.0',remote_upgrade_ready=True,dispatch_enabled=False)
        before=self.host(host);self.success(self.create(host));offer=self.offer(host)
        self.success(self.installing(host,offer));self.success(self.report(host,offer,success=False,version='0.5.0',message='旧版已恢复运行'))
        self.assertTrue(self.host(host)['upgrade_pending'])
        self.heartbeat(host,agent_version='0.5.0',remote_upgrade_ready=True,dispatch_enabled=False)
        after=self.host(host);self.assertEqual(after['agent_update']['status'],'failed')
        self.assertEqual(after['dispatch_enabled'],before['dispatch_enabled']);self.assertFalse(after['agent_dispatch_enabled'])
        self.assertFalse(after['upgrade_pending'])

    def test_repeated_request_and_lost_result_response_are_idempotent(self):
        host=self.ready();key=uuid.uuid4().hex
        with ThreadPoolExecutor(max_workers=2) as pool:jobs=list(pool.map(lambda _:self.success(self.create(host,key)),range(2)))
        self.assertEqual(jobs[0]['id'],jobs[1]['id'])
        offer=self.offer(host);self.success(self.installing(host,offer));self.success(self.installing(host,offer))
        self.success(self.report(host,offer));self.success(self.report(host,offer))
        self.heartbeat(host,agent_version='0.5.1',remote_upgrade_ready=True)
        self.assertEqual(self.success(self.report(host,offer))['status'],'succeeded')
        self.assertEqual(self.report(host,offer,success=False,version='0.5.0').status_code,409)

    def test_only_super_admin_same_origin_can_request_upgrade(self):
        host=self.ready()
        self.assertEqual(self.create(host,user=None).status_code,401)
        self.assertEqual(self.create(host,user='member').status_code,403)
        database.execute("UPDATE users SET role='admin' WHERE username='admin'")
        self.assertEqual(self.create(host).status_code,403)
        database.execute("UPDATE users SET role='super_admin' WHERE username='admin'")
        self.assertEqual(self.create(host,origin=False).status_code,403)
        self.assertEqual(self.create(host,headers={'Origin':'http://untrusted.example'}).status_code,403)

    def test_old_offline_busy_untrusted_and_current_agents_refuse_before_job_creation(self):
        host=self.register();self.assertEqual(self.create(host).status_code,409)
        self.heartbeat(host,agent_version='0.5.0',remote_upgrade_ready=False);self.assertEqual(self.create(host).status_code,409)
        self.heartbeat(host,agent_version='0.5.1',remote_upgrade_ready=True);self.assertEqual(self.create(host).status_code,409)
        self.heartbeat(host,agent_version='0.5.0',remote_upgrade_ready=True)
        stale=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        database.execute('UPDATE agent_hosts SET last_seen_at=? WHERE id=?',(stale,host));self.assertEqual(self.create(host).status_code,409)
        self.heartbeat(host,agent_version='0.5.0',remote_upgrade_ready=True);run=self.queue(host);self.heartbeat(host,agent_version='0.5.0',remote_upgrade_ready=True)
        self.assertEqual(self.create(host).status_code,409);self.assertEqual(self.host(host)['active_run_id'],run['id'])
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM agent_update_jobs')['n'],0)

    def test_expired_unclaimed_request_releases_but_downloaded_unknown_result_stays_blocked(self):
        host=self.ready();first=self.success(self.create(host))
        database.execute('UPDATE agent_update_jobs SET expires_at=1 WHERE id=?',(first['id'],))
        self.assertEqual(self.host(host)['agent_update']['status'],'expired');self.assertFalse(self.host(host)['upgrade_pending'])
        second=self.success(self.create(host));offer=self.offer(host);self.assertEqual(self.package(host,offer).status_code,200)
        database.execute('UPDATE agent_update_jobs SET expires_at=1 WHERE id=?',(second['id'],))
        self.assertEqual(self.host(host)['agent_update']['status'],'uncertain');self.assertTrue(self.host(host)['upgrade_pending'])
        self.assertIsNone(self.heartbeat(host,agent_version='0.5.0',remote_upgrade_ready=True)['run'])
        self.assertEqual(self.create(host).status_code,409)

    def test_wrong_host_token_cookie_and_revocation_cannot_download_or_report(self):
        host=self.ready();other=self.ready();self.success(self.create(host));offer=self.offer(host);d=offer['descriptor']
        self.assertEqual(self.package(other,offer).status_code,404)
        self.assertEqual(self.request('GET',f"/api/agent/updates/{d['id']}/package").status_code,401)
        bad=json.loads(json.dumps(offer));bad['descriptor']['token']='a'*32
        self.assertEqual(self.installing(host,bad).status_code,409)
        self.success(self.request('POST',f'/api/hosts/{host}/revoke'))
        self.assertEqual(self.package(host,offer).status_code,403);self.assertEqual(self.installing(host,offer).status_code,403)

    def test_manual_resume_cannot_release_remote_job_or_expired_authorization(self):
        host=self.ready();self.success(self.create(host));offer=self.offer(host);d=offer['descriptor']
        self.assertEqual(self.agent(host,'POST','/api/agent/upgrade-pause',{'token':d['token']}).status_code,409)
        self.success(self.installing(host,offer))
        self.success(self.agent(host,'POST','/api/agent/upgrade-pause',{'token':d['token']}))
        self.assertTrue(self.success(self.agent(host,'POST','/api/agent/upgrade-resume',{'token':d['token']}))['paused'])
        self.success(self.report(host,offer,success=False,version='0.5.0'));self.heartbeat(host,agent_version='0.5.0',remote_upgrade_ready=True)
        self.assertEqual(self.agent(host,'POST','/api/agent/upgrade-pause',{'token':d['token']}).status_code,409)

    def test_success_without_installing_and_conflicting_version_are_rejected(self):
        host=self.ready();self.success(self.create(host));offer=self.offer(host)
        self.assertEqual(self.report(host,offer).status_code,409)
        self.success(self.installing(host,offer));self.assertEqual(self.report(host,offer,version='9.0.0').status_code,409)
        self.assertEqual(self.report(host,offer,version='0.5.0').status_code,409)

    def test_audit_failure_rolls_back_job_reservation_and_package(self):
        host=self.ready()
        with patch.object(hosts,'_audit',side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):self.create(host)
        self.assertFalse(self.host(host)['upgrade_pending'])
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM agent_update_jobs')['n'],0)

    def test_upgrade_and_offer_race_never_authorizes_both(self):
        host=self.ready();run=self.queue(host)
        with ThreadPoolExecutor(max_workers=2) as pool:
            create=pool.submit(self.create,host);heartbeat=pool.submit(self.heartbeat,host,agent_version='0.5.0',remote_upgrade_ready=True)
            c=create.result();h=heartbeat.result()
        if c.status_code==200:self.assertIsNone(h['run'])
        else:self.assertEqual(c.status_code,409);self.assertEqual(h['run']['id'],run['id'])

    def test_late_durable_local_claim_cancels_unstarted_upgrade_and_continues_original_run(self):
        host=self.ready();run=self.queue(host);self.heartbeat(host,agent_version='0.5.0',remote_upgrade_ready=True)
        stale=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        database.execute('UPDATE agent_hosts SET last_seen_at=? WHERE id=?',(stale,host))
        self.assertIsNone(self.host(host)['active_run_id'])
        database.execute('UPDATE agent_hosts SET last_seen_at=? WHERE id=?',(database.utc_now(),host))
        self.success(self.create(host))
        response=self.heartbeat(host,agent_version='0.5.0',remote_upgrade_ready=True,active_run_id=run['id'])
        self.assertEqual(response['host']['agent_update']['status'],'cancelled')
        self.assertFalse(response['host']['upgrade_pending']);self.assertIsNone(response['update'])
        self.assertEqual(self.agent(host,'POST',f"/api/agent/runs/{run['id']}/claim").status_code,200)
