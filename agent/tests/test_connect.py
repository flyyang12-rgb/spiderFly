"""Real PowerShell connect orchestration against a synthetic Agent process."""
import json
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell only')
class ConnectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='spiderfly-connect-')
        self.root = Path(self.temp.name)
        self.install = self.root/'Agent'; self.install.mkdir()
        self.data = self.root/'State'; self.data.mkdir()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.stop)
        shutil.copy2(SOURCE/'manage.ps1',self.install/'manage.ps1')
        shutil.copy2(SOURCE/'setup.ps1',self.install/'setup.ps1')
        self.python = self.install/'.venv/Scripts/python.exe'
        subprocess.run([sys._base_executable,'-m','venv','--without-pip',str(self.install/'.venv')],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
        module = self.install/'spiderfly_agent'; module.mkdir()
        (module/'__init__.py').write_text('')
        (module/'__main__.py').write_text('''import json,os,sys,time
from pathlib import Path
root=Path(sys.argv[sys.argv.index('--data-dir')+1]);root.mkdir(exist_ok=True)
command=next(x for x in ('registration-status','setup','run') if x in sys.argv)
with (root/'calls.jsonl').open('a') as f:f.write(json.dumps({'command':command})+'\\n')
state=root/'test-state.json'
if command=='registration-status':
    print(state.read_text() if state.exists() else json.dumps({'state':'missing','message':'new code required'}))
elif command=='setup':
    if next(x.split('=',1)[1] for x in sys.argv if x.startswith('--code='))=='synthetic-invalid':raise SystemExit(7)
    state.write_text(json.dumps({'state':'pending','host_id':4,'name':'bot2'}))
else:
    (root/'shutdown.request').unlink(missing_ok=True)
    (root/'agent.pid.json').write_text(json.dumps({'pid':os.getpid(),'version':'0.5.1','protocol_version':1}))
    try:
        while not (root/'shutdown.request').exists():time.sleep(.05)
    finally:(root/'agent.pid.json').unlink(missing_ok=True)
''',encoding='utf-8')
        literal="'"+str(self.python).replace("'","''")+"'"
        (self.install/'start.ps1').write_text('''param([string]$DataDir,[string]$Server,[string]$Code,[string]$Name,[switch]$Dispatch,[switch]$PrepareOnly)
if ($PrepareOnly) { exit 0 }
& '''+literal+''' -m spiderfly_agent --data-dir $DataDir run
''',encoding='utf-8-sig')
        (self.data/'config.json').write_text('{"synthetic":"original config"}')
        (self.data/'identity.json').write_bytes(b'synthetic encrypted identity')
        (self.data/'journal.sqlite3').write_bytes(b'synthetic original journal')
        self.result = self.root/'result.json'
        self.env = {k:v for k,v in os.environ.items() if k.upper() not in {'PYTHONPATH','PYTHONHOME','PSMODULEPATH'}
                    and not k.upper().startswith(('SPIDERFLY_','FEISHU_','DEEPSEEK_'))}

    def command(self, action, *extra):
        stdout=self.root/'command.out';stderr=self.root/'command.err'
        with stdout.open('wb') as out,stderr.open('wb') as err:
            result=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-ExecutionPolicy','Bypass',
                '-File',str(self.install/'manage.ps1'),action,'-DataDir',str(self.data),*extra],
                env=self.env,cwd=self.install,stdout=out,stderr=err,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
        return subprocess.CompletedProcess(result.args,result.returncode,stdout.read_bytes(),stderr.read_bytes())

    def connect(self, *extra):
        return self.command('Connect','-Name','bot2','-Server','http://controller:8383','-ResultPath',str(self.result),*extra)

    def stop(self):
        if (self.data/'agent.pid.json').exists():
            result=self.command('Stop')
            if result.returncode:raise AssertionError(result.stderr.decode(errors='replace'))

    def test_missing_registration_is_not_reported_as_started(self):
        result=self.connect()
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertEqual(json.loads(self.result.read_text())['state'],'requires_enrollment')
        self.assertFalse((self.data/'agent.pid.json').exists())

    def test_existing_agent_stays_running_when_registration_is_valid(self):
        (self.data/'test-state.json').write_text(json.dumps({'state':'approved','host_id':2,'name':'bot2'}))
        result=self.command('Start');self.assertEqual(result.returncode,0,result.stderr)
        before=json.loads((self.data/'agent.pid.json').read_text())['pid']
        result=self.connect();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads((self.data/'agent.pid.json').read_text())['pid'],before)
        self.assertEqual(json.loads(self.result.read_text())['state'],'approved')

    def test_new_code_safely_stops_old_agent_reapplies_and_reports_pending(self):
        self.assertEqual(self.command('Start').returncode,0)
        before=json.loads((self.data/'agent.pid.json').read_text())['pid']
        result=self.connect('-Code','synthetic-new-code-123')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(self.result.read_text())['state'],'pending')
        self.assertNotEqual(json.loads((self.data/'agent.pid.json').read_text())['pid'],before)
        calls=[json.loads(line)['command'] for line in (self.data/'calls.jsonl').read_text().splitlines()]
        self.assertEqual(calls.count('setup'),1)
        self.assertEqual((self.data/'identity.json').read_bytes(),b'synthetic encrypted identity')
        self.assertEqual((self.data/'journal.sqlite3').read_bytes(),b'synthetic original journal')

    def test_invalid_code_does_not_start_an_agent_or_claim_success(self):
        result=self.connect('-Code','synthetic-invalid')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(json.loads(self.result.read_text())['state'],'error')
        self.assertFalse((self.data/'agent.pid.json').exists())

    def test_unfinished_work_refuses_reapplication_without_stopping_current_agent(self):
        (self.data/'test-state.json').write_text(json.dumps({'state':'approved','host_id':2,'name':'bot2','can_reapply':False}))
        self.assertEqual(self.command('Start').returncode,0)
        before=json.loads((self.data/'agent.pid.json').read_text())['pid']
        result=self.connect('-Code','synthetic-new-code-123')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(json.loads((self.data/'agent.pid.json').read_text())['pid'],before)

    def use_real_preparation(self):
        shutil.copy2(SOURCE/'start.ps1',self.install/'start.ps1')
        (self.install/'requirements.txt').write_text('# synthetic bootstrap requirements\n')
        (self.install/'pip.py').write_text('''import os,sys
from pathlib import Path
print('[notice] A new release of pip is available',file=sys.stderr)
if sys.argv[1] == os.environ.get('FAKE_PIP_FAIL_MODE'):
    print('ERROR: synthetic dependency download failed',file=sys.stderr)
    raise SystemExit(7)
''')

    def wizard_connect(self):
        quote=lambda value: "'"+str(value).replace("'","''")+"'"
        command=('& '+quote(self.install/'manage.ps1')+' Connect -DataDir '+quote(self.data)
                 +' -ResultPath '+quote(self.result)+" -Name 'bot2' -Server 'http://controller:8383' -Code 'synthetic-new-code'"
                 +' *>&1 | Out-File -Encoding utf8 -LiteralPath '+quote(self.root/'wizard.log')+'; exit $LASTEXITCODE')
        with (self.root/'wrapper.out').open('wb') as out,(self.root/'wrapper.err').open('wb') as err:
            return subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',
                base64.b64encode(command.encode('utf-16le')).decode()],env=self.env,cwd=self.install,
                stdout=out,stderr=err,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)

    def test_wizard_real_preparation_accepts_notices_and_recovers_partial_install(self):
        self.use_real_preparation()
        # An earlier interrupted install left python.exe without dependencies.
        module=self.install/'spiderfly_agent/__main__.py'
        module.write_text("from pathlib import Path\n"
                          "assert (Path.cwd()/'.venv/.dependencies.sha256').exists(), 'imports unavailable before preparation'\n"
                          +module.read_text(),encoding='utf-8')
        result=self.wizard_connect()
        self.assertEqual(result.returncode,0,(self.root/'wizard.log').read_text(encoding='utf-8-sig'))
        self.assertEqual(json.loads(self.result.read_text())['state'],'pending')
        self.assertTrue((self.data/'agent.pid.json').exists())

    def test_wizard_real_dependency_failure_reports_reason_and_same_button_retries(self):
        self.use_real_preparation()
        self.env['FAKE_PIP_FAIL_MODE']='install'
        result=self.wizard_connect()
        self.assertNotEqual(result.returncode,0)
        value=json.loads(self.result.read_text(encoding='utf-8'))
        self.assertEqual(value['state'],'error')
        self.assertIn('synthetic dependency download failed',value['message'])
        self.assertIn('退出码 7',value['message'])
        self.assertFalse((self.data/'agent.pid.json').exists())
        self.assertFalse((self.install/'.venv/.dependencies.sha256').exists())
        self.env.pop('FAKE_PIP_FAIL_MODE')
        result=self.wizard_connect()
        self.assertEqual(result.returncode,0,(self.root/'wizard.log').read_text(encoding='utf-8-sig'))
        self.assertEqual(json.loads(self.result.read_text())['state'],'pending')

    def test_fresh_download_can_reapply_while_previous_folder_agent_is_running(self):
        (self.data/'test-state.json').write_text(json.dumps({'state':'approved','host_id':2,'name':'bot2','can_reapply':True}))
        self.assertEqual(self.command('Start').returncode,0)
        before=json.loads((self.data/'agent.pid.json').read_text())['pid']
        previous=self.install
        self.install=self.root/'重新下载 Agent'
        self.install.mkdir()
        for name in ('manage.ps1','setup.ps1'):
            shutil.copy2(SOURCE/name,self.install/name)
        shutil.copytree(previous/'spiderfly_agent',self.install/'spiderfly_agent')
        self.use_real_preparation()
        self.assertFalse((self.install/'.venv').exists())
        # Match the standard Python installer/PATH lookup on this test machine.
        self.env['PATH']=str(Path(sys._base_executable).parent)+os.pathsep+self.env['PATH']
        result=self.wizard_connect()
        self.assertEqual(result.returncode,0,(self.root/'wizard.log').read_text(encoding='utf-8-sig'))
        self.assertEqual(json.loads(self.result.read_text())['state'],'pending')
        self.assertNotEqual(json.loads((self.data/'agent.pid.json').read_text())['pid'],before)
        self.assertEqual((self.data/'identity.json').read_bytes(),b'synthetic encrypted identity')
