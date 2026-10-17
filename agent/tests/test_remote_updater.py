"""Pinned signatures, bounded downloads and durable handoff; synthetic identities only."""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import requests

from spiderfly_agent import __version__
from spiderfly_agent.api import Api, ApiError
from spiderfly_agent.identity import save_json
from spiderfly_agent.updater import RemoteUpdater, canonical
import spiderfly_agent.updater as implementation


class RemoteUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='spiderfly remote updater ');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'State';self.data.mkdir();self.program=self.root/'Program';self.program.mkdir()
        self.key=Ed25519PrivateKey.generate()
        self.public={'format':1,'public_key':base64.b64encode(self.key.public_key().public_bytes_raw()).decode()}
        save_json(self.program/'controller-trust.json',self.public)
        self.environment=patch.dict(os.environ,{'LOCALAPPDATA':str(self.root/'Local')});self.environment.start();self.addCleanup(self.environment.stop)
        self.api=Mock();self.api.host_id=5;self.api.server='http://127.0.0.1:18763';self.api.key=Ed25519PrivateKey.generate()
        self.updater=RemoteUpdater(self.api,self.data,'synthetic-machine',self.program)
        self.api.request.return_value={'status':'awaiting_reconnect'}
        self.descriptor={'format':1,'id':'a'*32,'host_id':5,'machine_id':'synthetic-machine','from_version':__version__,
                         'target_version':'0.5.1','token':'b'*32,'sha256':hashlib.sha256(b'package').hexdigest(),
                         'size':7,'expires_at':time.time()+600}

    def offer(self,**changes):
        d={**self.descriptor,**changes}
        return {'descriptor':d,'signature':base64.b64encode(self.key.sign(canonical(d))).decode()}

    def accept(self):
        with patch.object(self.updater,'private_directory',lambda p:p.mkdir(parents=True,exist_ok=True)):
            self.updater.accept(self.offer())
        return json.loads(self.updater.marker.read_text('utf-8'))

    def test_trust_bootstraps_once_and_never_silently_replaces_pinned_controller(self):
        self.assertTrue(self.updater.ready)
        original=(self.data/'controller-update-key.json').read_bytes()
        other=Ed25519PrivateKey.generate();save_json(self.program/'controller-trust.json',{'format':1,'public_key':base64.b64encode(other.public_key().public_bytes_raw()).decode()})
        changed=RemoteUpdater(self.api,self.data,'synthetic-machine',self.program)
        self.assertFalse(changed.ready);self.assertEqual((self.data/'controller-update-key.json').read_bytes(),original)

    def test_signature_tampering_wrong_host_expiry_and_downgrade_refuse_before_handoff(self):
        bad=self.offer();bad['descriptor']['sha256']='c'*64
        for offer in (bad,self.offer(host_id=7),self.offer(machine_id='another'),self.offer(expires_at=1),
                      self.offer(target_version=__version__),self.offer(from_version='0.4.1')):
            with self.subTest(offer=offer['descriptor']['target_version']),self.assertRaises(ValueError):
                self.updater.accept(offer)
        self.assertFalse(self.updater.marker.exists())

    def test_private_windows_job_directory_uses_acl(self):
        if os.name!='nt':self.skipTest('Windows ACL')
        job=self.updater.job_directory('a'*32);self.updater.private_directory(job)
        self.assertTrue(job.is_dir());self.assertTrue(job.is_relative_to(self.root))

    def test_download_and_installing_handoff_are_persisted_and_launch_only_once(self):
        state=self.accept();job=self.updater.job_directory('a'*32)
        self.updater.launch=Mock()
        transport=Mock();transport.download.side_effect=lambda path,destination,**kw:destination.write_bytes(b'package')
        with patch.object(implementation,'Api',return_value=transport):self.updater.prepare(state,job)
        self.updater.launch.assert_called_once_with(job,'b'*32)
        self.assertEqual(json.loads(self.updater.marker.read_text())['phase'],'installing')
        self.updater.poll();self.updater.accept(self.offer())
        self.assertEqual(self.updater.launch.call_count,1)

    def test_network_failure_before_replacement_keeps_a_retryable_receipt(self):
        state=self.accept();transport=Mock();transport.download.side_effect=requests.ConnectionError('synthetic offline')
        self.updater.launch=Mock()
        with patch.object(implementation,'Api',return_value=transport):self.updater.prepare(state,self.updater.job_directory('a'*32))
        self.assertEqual(json.loads(self.updater.marker.read_text())['phase'],'accepted');self.updater.launch.assert_not_called()

    def test_bad_package_or_launch_failure_retains_failure_result_for_signed_report(self):
        state=self.accept();transport=Mock();transport.download.side_effect=ValueError('升级包校验失败');self.updater.launch=Mock()
        with patch.object(implementation,'Api',return_value=transport):self.updater.prepare(state,self.updater.job_directory('a'*32))
        self.updater.launch.assert_not_called();self.updater.poll()
        payload=self.api.request.call_args.args[2];self.assertFalse(payload['success']);self.assertIn('校验失败',payload['message'])
        self.assertTrue(self.updater.marker.exists())

    def test_restarted_new_version_reports_only_after_finished_local_progress(self):
        state=self.accept();state['phase']='installing';save_json(self.updater.marker,state)
        self.updater.poll();self.api.request.assert_not_called()
        job=self.updater.job_directory('a'*32);save_json(job/'progress.json',{'done':True,'success':True,'message':'已升级至 0.5.1'})
        with patch.object(implementation,'__version__','0.5.1'):self.updater.poll()
        payload=self.api.request.call_args.args[2];self.assertTrue(payload['success']);self.assertEqual(payload['agent_version'],'0.5.1')
        self.updater.observe({'agent_update':{'id':'a'*32,'status':'succeeded'}})
        self.assertFalse(self.updater.marker.exists())

    def test_failed_old_version_reports_failure_and_offline_report_retries(self):
        state=self.accept();state['phase']='installing';save_json(self.updater.marker,state)
        save_json(self.updater.job_directory('a'*32)/'progress.json',{'done':True,'success':False,'message':'旧版已恢复运行'})
        self.api.request.side_effect=ApiError(503,'offline')
        with self.assertRaises(ApiError):self.updater.poll()
        self.assertTrue(self.updater.marker.exists())
        self.api.request.side_effect=None;self.api.request.return_value={'status':'awaiting_reconnect'};self.updater.poll()
        self.assertFalse(self.api.request.call_args.args[2]['success'])
        self.assertEqual(self.api.request.call_args.args[2]['agent_version'],__version__)

    def test_client_accepts_upgrade_offer_without_starting_business_worker(self):
        from spiderfly_agent.client import Client
        from spiderfly_agent.journal import Journal
        journal=Journal(self.data/'journal.sqlite3');self.addCleanup(journal.close)
        updater=Mock();updater.ready=True
        self.api.request.return_value={'host':{'state':'upgrading','approval_status':'approved'},'run':None,'update':self.offer()}
        client=Client(self.api,journal,self.data,dispatch=True,session_check=lambda:True,updater=updater)
        client.tick();updater.accept.assert_called_once();self.assertIsNone(client.worker);self.assertIsNone(journal.active())

    def test_failed_upgrade_report_cannot_prevent_heartbeat_and_control_refresh(self):
        from spiderfly_agent.client import Client
        from spiderfly_agent.journal import Journal
        journal=Journal(self.data/'journal.sqlite3');self.addCleanup(journal.close)
        updater=Mock();updater.ready=True;updater.poll.side_effect=ApiError(503,'synthetic report failure')
        self.api.request.return_value={'host':{'state':'upgrading','approval_status':'approved'},'run':None}
        client=Client(self.api,journal,self.data,session_check=lambda:True,updater=updater)
        with self.assertRaises(ApiError):client.tick()
        self.assertEqual(self.api.request.call_args.args[:2],('POST','/api/agent/heartbeat'))
        updater.observe.assert_called_once()


class BoundedUpdateDownloadTests(unittest.TestCase):
    def test_signed_bounded_stream_rejects_tampering_overflow_and_redirects(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);api=Api('http://127.0.0.1:18763',Ed25519PrivateKey.generate(),1)
            self.addCleanup(api.session.close)
            for chunks,status,success in (([b'good'],200,True),([b'bad!'],200,False),([b'good',b'x'],200,False),([b'good'],302,False)):
                response=Mock();response.status_code=status;response.iter_content.return_value=iter(chunks)
                response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
                path=root/'package.zip';path.unlink(missing_ok=True)
                with patch.object(api.session,'request',return_value=response) as request:
                    if success:api.download('/api/agent/updates/1/package',path,size=4,sha256=hashlib.sha256(b'good').hexdigest())
                    else:
                        with self.assertRaises((ValueError,ApiError)):api.download('/api/agent/updates/1/package',path,size=4,sha256=hashlib.sha256(b'good').hexdigest())
                    self.assertFalse(request.call_args.kwargs['allow_redirects'])
                    self.assertIn('X-SpiderFly-Signature',request.call_args.kwargs['headers'])
                self.assertEqual(path.exists(),success);self.assertFalse(path.with_suffix('.partial').exists())
