"""Exercise the real WinForms click/timer handlers with isolated fake services."""
import json
import os
import subprocess
import unittest

from tests import test_connect


@unittest.skipUnless(os.name == 'nt', 'Windows WinForms only')
class SetupWizardTests(unittest.TestCase):
    def test_saved_utf8_chinese_name_is_prefilled_without_corruption(self):
        fixture = test_connect.ConnectTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        (fixture.data/'config.json').write_text(json.dumps({'server':'http://controller:8383',
            'name':'全新安装 bot2'},ensure_ascii=False),encoding='utf-8')
        source = (test_connect.SOURCE/'setup.ps1').read_text(encoding='utf-8-sig')
        probe = "[IO.File]::WriteAllText((Join-Path $dataRoot 'prefill.txt'),$machineName.Text,[Text.UTF8Encoding]::new($false))"
        (fixture.install/'setup-probe.ps1').write_text(source.replace('[void]$form.ShowDialog()',probe),encoding='utf-8-sig')
        result = subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-STA',
            '-File',str(fixture.install/'setup-probe.ps1'),'-DataDir',str(fixture.data)],
            cwd=fixture.install,env=fixture.env,capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((fixture.data/'prefill.txt').read_text(encoding='utf-8'),'全新安装 bot2')

    def test_dependency_failure_is_visible_and_original_button_can_retry(self):
        fixture = test_connect.ConnectTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.use_real_preparation()
        (fixture.data/'config.json').unlink()
        fixture.env['FAKE_PIP_FAIL_MODE'] = 'install'
        probe = r'''
# Test harness only: never edit the real Windows Startup folder.
function Set-StartupShortcut([bool]$Enabled, [bool]$Dispatch) {
    [IO.File]::WriteAllText((Join-Path $dataRoot 'startup-called'), 'yes')
}
function Wait-TestOperation {
    $deadline = (Get-Date).AddSeconds(40)
    while ($script:process -and (Get-Date) -lt $deadline) {
        [Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 30
    }
    if ($script:process) { throw 'Test wizard operation timed out' }
}
$form.ShowInTaskbar=$false
$form.StartPosition='Manual'
$form.Location=[Drawing.Point]::new(-3000,-3000)
$form.Show()
$server.Text='http://controller:8383'
$machineName.Text='bot2'
$code.Text='synthetic-new-code'
$autoStart.Checked=$false
$install.PerformClick()
Wait-TestOperation
$failure=@{message=$status.Text; retry_enabled=$install.Enabled; code_enabled=$code.Enabled;
    name_enabled=$machineName.Enabled; details_visible=$detailsLink.Visible;
    log_exists=(Test-Path -LiteralPath $setupLog); startup_changed=(Test-Path (Join-Path $dataRoot 'startup-called'))}
Remove-Item Env:FAKE_PIP_FAIL_MODE
$install.PerformClick()
Wait-TestOperation
$success=@{message=$status.Text; button=$install.Text; code=$code.Text;
    startup_updated=(Test-Path (Join-Path $dataRoot 'startup-called'))}
[IO.File]::WriteAllText((Join-Path $dataRoot 'wizard-probe.json'),
    (@{failure=$failure; success=$success} | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
$form.Close()
'''
        source = (test_connect.SOURCE/'setup.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('[void]$form.ShowDialog()', source)
        (fixture.install/'setup-probe.ps1').write_text(
            source.replace('[void]$form.ShowDialog()', probe), encoding='utf-8-sig')
        with (fixture.root/'gui.out').open('wb') as out, (fixture.root/'gui.err').open('wb') as err:
            result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-STA', '-File', str(fixture.install/'setup-probe.ps1'), '-DataDir', str(fixture.data)],
                cwd=fixture.install, env=fixture.env, stdout=out, stderr=err, timeout=90,
                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, (fixture.root/'gui.err').read_bytes())
        value = json.loads((fixture.data/'wizard-probe.json').read_text(encoding='utf-8'))
        self.assertIn('synthetic dependency download failed', value['failure']['message'])
        for key in ('retry_enabled', 'code_enabled', 'name_enabled', 'details_visible', 'log_exists'):
            self.assertTrue(value['failure'][key], key)
        self.assertFalse(value['failure']['startup_changed'])
        self.assertIn('接入申请已提交', value['success']['message'])
        self.assertEqual(value['success']['button'], '完成')
        self.assertEqual(value['success']['code'], '')
        self.assertTrue(value['success']['startup_updated'])
