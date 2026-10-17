"""Real PowerShell updater pipeline against synthetic local Agent processes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from spiderfly_agent import __version__ as CURRENT_VERSION

SOURCE = Path(__file__).resolve().parents[1]
PROGRAM = ('README.md','requirements.txt','start.ps1','manage.ps1','setup.ps1',
           'install-python.ps1','安装并接入Agent.bat','upgrade.ps1','upgrade-core.ps1',
           'upgrade-ui.ps1','upgrade-transport.py','升级Agent.bat','remote-upgrade.ps1')


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell updater only')
class UpgradePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='spiderfly upgrade test ')
        self.addCleanup(self.remove_fixture)
        self.root = Path(self.temp.name)
        self.install = self.root/'Old Agent'; self.install.mkdir()
        self.data = self.root/'State'; self.data.mkdir()
        self.package = self.root/'New package'; self.package.mkdir()
        self.job = self.root/'Upgrade job'
        (self.install/'spiderfly_agent').mkdir()
        (self.install/'spiderfly_agent/__init__.py').write_text('__version__ = "0.4.0"\n')
        (self.install/'requirements.txt').write_text('# old fixture\n')
        for name in ('manage.ps1','setup.ps1','install-python.ps1','安装并接入Agent.bat','README.md'):
            shutil.copy2(SOURCE/name,self.install/name)
        venv=self.install/'.venv';(venv/'Scripts').mkdir(parents=True)
        (venv/'Scripts/python.exe').touch()
        (venv/'pyvenv.cfg').write_text('executable = '+sys._base_executable+'\n')
        (self.data/'identity.json').write_text('{"synthetic":"keep identity"}')
        (self.data/'config.json').write_text('{"synthetic":"keep config"}')
        self.original={n:(self.data/n).read_bytes() for n in ('identity.json','config.json')}
        module=self.root/'spiderfly_agent';module.mkdir()
        (module/'__main__.py').write_text(
            'import json,os,sys,time\nfrom pathlib import Path\n'
            'root=Path(sys.argv[sys.argv.index("--data-dir")+1]);root.mkdir(exist_ok=True)\n'
            'version=sys.argv[sys.argv.index("--version")+1]\n'
            '(root/"shutdown.request").unlink(missing_ok=True)\n'
            '(root/"agent.pid.json").write_text(json.dumps({"pid":os.getpid(),"version":version,"protocol_version":1}))\n'
            'try:\n    while not (root/"shutdown.request").exists():time.sleep(.05)\n'
            'finally:(root/"agent.pid.json").unlink(missing_ok=True)\n')
        self.start_script(self.install,'0.4.0')
        for name in PROGRAM:
            shutil.copy2(SOURCE/name,self.package/name)
        shutil.copytree(SOURCE/'spiderfly_agent',self.package/'spiderfly_agent',ignore=shutil.ignore_patterns('__pycache__'))
        self.start_script(self.package,CURRENT_VERSION)
        (self.package/'controller-trust.json').write_text('{"format":1,"public_key":"YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="}')
        self.manifest()
        self.env={k:v for k,v in os.environ.items() if k.upper()!='PSMODULEPATH'}
        self.env['PYTHONPATH']=str(self.root)
        self.addCleanup(self.stop)

    def start_script(self,directory,version,fail=False):
        literal="'"+sys.executable.replace("'","''")+"'"
        (directory/'start.ps1').write_text(
            'param([string]$DataDir,[string]$Server,[string]$Code,[string]$Name,[switch]$Dispatch)\n'+
            ('exit 9\n' if fail else f'& {literal} -m spiderfly_agent --data-dir $DataDir run --version {version}\n'),
            encoding='utf-8-sig')

    def remove_fixture(self):
        assert self.root.resolve().parent==Path(tempfile.gettempdir()).resolve()
        assert self.root.name.startswith('spiderfly upgrade test ')
        deadline=time.monotonic()+5
        while True:
            try:self.temp.cleanup();return
            except PermissionError:
                if time.monotonic()>=deadline:raise
                time.sleep(.05)

    def manifest(self,version=CURRENT_VERSION):
        files={p.relative_to(self.package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
               for p in self.package.rglob('*') if p.is_file() and p.name!='update-manifest.json'}
        (self.package/'update-manifest.json').write_text(json.dumps({'format':1,'version':version,'files':files}),encoding='utf-8')

    def ps(self,script,timeout=55):
        path=self.root/'driver.ps1'
        path.write_text('[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n'+script,encoding='utf-8-sig')
        out=self.root/'driver.out';err=self.root/'driver.err'
        with out.open('wb') as stdout,err.open('wb') as stderr:
            result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(path)],
                                  cwd=self.root,env=self.env,stdout=stdout,stderr=stderr,
                                  timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW)
        return subprocess.CompletedProcess(result.args,result.returncode,out.read_bytes(),err.read_bytes())

    @staticmethod
    def quote(path):return "'"+str(path).replace("'","''")+"'"

    def stop(self):
        if not (self.data/'agent.pid.json').exists():return
        # This shutdown file belongs only to our synthetic subprocess. Keep its
        # directory until that process exits, even if the updater test failed.
        (self.data/'shutdown.request').write_text('fixture cleanup')
        deadline=time.monotonic()+10
        while (self.data/'agent.pid.json').exists() and time.monotonic()<deadline:
            time.sleep(.05)
        self.assertFalse((self.data/'agent.pid.json').exists(),'Synthetic Agent did not exit')

    def upgrade(self,mode='success',package=None):
        script=f'''
. {self.quote(SOURCE/'upgrade-core.ps1')}
$script:fixtureMode='{mode}'
$script:transportLog={self.quote(self.root/'transport.log')}
function Invoke-UpgradeTransport([string]$Python,[string]$Helper,[string]$Data,[string]$Action,[string]$Token){{
 Add-Content -LiteralPath $script:transportLog -Value $Action
 if($Action -eq 'inspect'){{return @{{unacknowledged=$(if($script:fixtureMode -eq 'busy'){{1}}else{{0}})}}}}
 if($Action -eq 'pause'){{if($script:fixtureMode -eq 'lost-pause'){{throw 'synthetic pause response lost'}};return @{{dispatch_enabled=$true}}}}
 return @{{paused=$false}}
}}
Invoke-AgentUpgrade -InstallDir {self.quote(self.install)} -DataDir {self.quote(self.data)} -PackagePath {self.quote(package or self.package)} -JobDir {self.quote(self.job)}
'''
        result=self.ps(script)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
        progress=json.loads((self.job/'progress.json').read_text('utf-8-sig'))
        for name,value in self.original.items():self.assertEqual((self.data/name).read_bytes(),value)
        return progress

    def test_real_upgrade_stops_backs_up_replaces_and_restarts_same_data(self):
        started=self.ps(f"& {self.quote(self.package/'manage.ps1')} Start -InstallDir {self.quote(self.install)} -DataDir {self.quote(self.data)}")
        self.assertEqual(started.returncode,0,started.stderr.decode('utf-8','replace'))
        archive=self.root/'Agent download.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for file in self.package.rglob('*'):
                if file.is_file():z.write(file,'SpiderFlyAgent/'+file.relative_to(self.package).as_posix())
        progress=self.upgrade(package=archive)
        self.assertTrue(progress['success'],progress)
        self.assertEqual(json.loads((self.data/'agent.pid.json').read_text())['version'],CURRENT_VERSION)
        self.assertTrue((self.job/'state-before.zip').is_file())
        self.assertTrue((self.job/'program-before.zip').is_file())
        self.assertFalse((self.data/'upgrade.resume.json').exists())
        self.assertEqual((self.root/'transport.log').read_text().splitlines().count('resume'),1)

    def test_failed_new_start_restores_old_program_and_running_version(self):
        self.start_script(self.package,CURRENT_VERSION,fail=True);self.manifest()
        progress=self.upgrade()
        self.assertFalse(progress['success'])
        self.assertIn('旧版已恢复运行',progress['message'])
        self.assertEqual(json.loads((self.data/'agent.pid.json').read_text())['version'],'0.4.0')
        self.assertIn('0.4.0',(self.install/'spiderfly_agent/__init__.py').read_text())
        self.assertFalse((self.install/'upgrade-core.ps1').exists())

    def test_unreturned_journal_refuses_upgrade_before_pause_or_stop(self):
        progress=self.upgrade('busy')
        self.assertFalse(progress['success']);self.assertIn('回传',progress['message'])
        self.assertEqual((self.root/'transport.log').read_text().splitlines(),['inspect'])
        self.assertFalse((self.job/'state-before.zip').exists())

    def test_lost_pause_response_is_released_without_changing_program(self):
        progress=self.upgrade('lost-pause')
        self.assertFalse(progress['success'])
        self.assertIn('resume',(self.root/'transport.log').read_text().splitlines())
        self.assertFalse((self.job/'state-before.zip').exists())

    def test_bad_file_hash_rejects_package_before_touching_agent(self):
        (self.package/'requirements.txt').write_text('tampered')
        progress=self.upgrade()
        self.assertFalse(progress['success']);self.assertIn('校验失败',progress['message'])
        self.assertFalse((self.root/'transport.log').exists())

    def test_bad_zip_path_cannot_write_outside_job(self):
        path=self.root/'bad.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr('SpiderFlyAgent/../../escape.txt','bad')
        progress=self.upgrade(package=path)
        self.assertFalse(progress['success'])
        self.assertFalse((self.root/'escape.txt').exists())

    def test_equal_or_older_package_does_not_stop_agent(self):
        (self.package/'spiderfly_agent/__init__.py').write_text('__version__ = "0.4.0"\n')
        self.manifest('0.4.0')
        progress=self.upgrade()
        self.assertFalse(progress['success']);self.assertIn('更新版本',progress['message'])
        self.assertFalse((self.root/'transport.log').exists())

    def test_snapshot_ignores_venv_and_unrelated_files(self):
        (self.install/'.venv/private.py').write_text('not program')
        result=self.ps(f". {self.quote(SOURCE/'upgrade-core.ps1')}; Get-UpgradeProgramHashes {self.quote(self.install)} | ConvertTo-Json")
        self.assertEqual(result.returncode,0)
        hashes=json.loads(result.stdout.decode('utf-8-sig'))
        self.assertFalse(any('.venv' in name for name in hashes))

    def test_native_form_click_validation_async_progress_and_retry(self):
        (self.package/'requirements.txt').write_text('tampered')
        script=f'''
. {self.quote(SOURCE/'upgrade-core.ps1')}
. {self.quote(SOURCE/'upgrade-ui.ps1')}
$env:LOCALAPPDATA={self.quote(self.root/'Private local data')}
$form=New-AgentUpgradeForm {self.quote(SOURCE)} '' {self.quote(self.data)}
$form.StartPosition='Manual';$form.Location=[Drawing.Point]::new(-20000,-20000);$form.ShowInTaskbar=$false
try{{
 $form.Show();[Windows.Forms.Application]::DoEvents();$ctx=$form.Tag
 $ctx.action.PerformClick()
 if($ctx.status.Text -ne '请选择原 Agent 安装目录'){{throw 'Missing click validation'}}
 $ctx.install.Text={self.quote(self.install)};$ctx.package.Text={self.quote(self.package)}
 $ctx.action.PerformClick()
 if(-not $ctx.busy -or $ctx.action.Enabled -or $ctx.close.Enabled){{throw 'Busy controls not disabled'}}
 $form.Close();if($form.IsDisposed){{throw 'Form closed during upgrade'}}
 $deadline=[DateTime]::UtcNow.AddSeconds(30)
 while($ctx.busy -and [DateTime]::UtcNow -lt $deadline){{[Windows.Forms.Application]::DoEvents();Start-Sleep -Milliseconds 30}}
 if($ctx.busy -or -not $ctx.action.Enabled -or $ctx.action.Text -ne '重试'){{throw 'Async completion/retry unavailable'}}
 if($ctx.status.Text -notmatch '校验失败' -or -not $ctx.logs.Visible){{throw 'Failure/progress log not shown'}}
 if(-not $ctx.job.StartsWith($env:LOCALAPPDATA+'\\')){{throw 'Job escaped fixture'}}
 Write-Output 'Native form click, busy close protection, async completion and retry passed'
}}finally{{$form.Dispose()}}
'''
        result=self.ps(script)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))


class UpgradeResumeTests(unittest.TestCase):
    def test_client_retries_completed_upgrade_resume_before_accepting_work(self):
        from unittest.mock import Mock
        from spiderfly_agent import __version__
        from spiderfly_agent.api import ApiError
        from spiderfly_agent.client import Client
        from spiderfly_agent.journal import Journal
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);journal=Journal(root/'journal.sqlite3')
            try:
                marker=root/'upgrade.resume.json';marker.write_text(json.dumps({'version':__version__,'token':'a'*32}))
                api=Mock();api.request.side_effect=ApiError(503,'synthetic offline')
                client=Client(api,journal,root)
                with self.assertRaises(ApiError):client.tick()
                self.assertTrue(marker.exists())
                api.request.side_effect=[{'paused':False},{'host':{'state':'idle'},'run':None}]
                client.tick();self.assertFalse(marker.exists())
            finally:journal.close()
