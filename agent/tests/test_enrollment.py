import base64
import json
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from spiderfly_agent import enrollment
from spiderfly_agent.api import ApiError


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='spiderfly-enrollment-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.key = Ed25519PrivateKey.generate()
        self.old = {'server':'http://controller:8383','host_id':2,'name':'old-name'}
        (self.root/'config.json').write_text(json.dumps(self.old))
        (self.root/'identity.json').write_bytes(b'synthetic-encrypted-key')
        (self.root/'journal.sqlite3').write_bytes(b'synthetic-old-journal')
        self.enterContext(patch.object(enrollment,'load_identity',return_value=('original-machine-id',self.key)))
        self.api = self.enterContext(patch.object(enrollment,'Api')).return_value
        self.api.server = self.old['server']
        self.api.request.return_value = {'host_id':2,'approval_status':'pending','protocol_version':1,'name':'new-name'}

    def test_reapply_keeps_identity_and_journal_and_signs_fresh_request(self):
        enrollment.apply(self.root,self.old['server'],'synthetic-new-code','new-name')
        value = self.api.request.call_args.args[2].copy()
        signature = base64.b64decode(value.pop('reenrollment_signature'))
        self.key.public_key().verify(signature,json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())
        self.assertEqual(value['machine_id'],'original-machine-id')
        self.assertEqual((self.root/'identity.json').read_bytes(),b'synthetic-encrypted-key')
        self.assertEqual((self.root/'journal.sqlite3').read_bytes(),b'synthetic-old-journal')
        self.assertEqual(json.loads((self.root/'config.json').read_text())['name'],'new-name')

    def test_new_host_id_archives_old_events_instead_of_resending_as_new_host(self):
        self.api.request.return_value.update(host_id=4)
        (self.root/'journal.sqlite3-wal').write_bytes(b'old-wal')
        enrollment.apply(self.root,self.old['server'],'synthetic-new-code','new-name')
        archived = list((self.root/'registration-history').glob('*'))
        self.assertEqual(len(archived),1)
        self.assertEqual((archived[0]/'journal.sqlite3').read_bytes(),b'synthetic-old-journal')
        self.assertEqual((archived[0]/'journal.sqlite3-wal').read_bytes(),b'old-wal')
        self.assertFalse((self.root/'journal.sqlite3').exists())
        self.assertEqual(json.loads((self.root/'config.json').read_text())['host_id'],4)

    def test_failed_registration_or_invalid_response_keeps_old_config_and_journal(self):
        before = (self.root/'config.json').read_bytes()
        self.api.request.side_effect = ApiError(400,'invalid code')
        with self.assertRaises(ApiError):enrollment.apply(self.root,self.old['server'],'synthetic','new-name')
        self.api.request.side_effect = None
        self.api.request.return_value = {'host_id':4,'approval_status':'pending','protocol_version':2}
        with self.assertRaises(ValueError):enrollment.apply(self.root,self.old['server'],'synthetic','new-name')
        self.assertEqual((self.root/'config.json').read_bytes(),before)
        self.assertTrue((self.root/'journal.sqlite3').exists())

    def test_status_reports_revoked_and_deleted_without_modifying_files(self):
        self.api.request.return_value.update(approval_status='revoked')
        self.assertEqual(enrollment.registration_status(self.root)['state'],'revoked')
        self.api.request.side_effect = ApiError(404,'宿主机不存在')
        self.assertEqual(enrollment.registration_status(self.root)['state'],'missing')
        self.api.request.side_effect = ApiError(404,'Not Found')
        self.assertEqual(enrollment.registration_status(self.root)['state'],'error')
        self.assertTrue((self.root/'journal.sqlite3').exists())

    def test_other_controller_is_not_silently_given_existing_identity(self):
        self.api.server = 'http://other-controller:8383'
        with self.assertRaisesRegex(ValueError,'切换主控'):
            enrollment.apply(self.root,self.api.server,'synthetic','new-name')
        self.api.request.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Agent CLI supports Windows')
    def test_registration_cli_preserves_chinese_across_powershell_code_pages(self):
        from spiderfly_agent.__main__ import main
        value = {'state':'pending','name':'全新安装 bot2','host_id':2}
        output = io.StringIO()
        with patch('sys.argv',['agent','--data-dir',str(self.root),'registration-status']), \
                patch.object(enrollment,'registration_status',return_value=value), contextlib.redirect_stdout(output):
            main()
        transported = output.getvalue().encode('utf-8').decode('cp936')
        self.assertEqual(json.loads(transported),value)
