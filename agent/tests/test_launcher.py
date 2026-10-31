"""Exercise the real PowerShell launcher with isolated fake pip/Agent modules."""

import hashlib
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


_FAKE_PIP = '''
import json, os, sys
from pathlib import Path
root = Path.cwd()
with (root / "calls.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1]) + "\\n")
if sys.argv[1] == os.environ.get("FAKE_PIP_FAIL_MODE") and not (root / "failure_seen").exists():
    (root / "failure_seen").write_text("1")
    print("ERROR: synthetic dependency download failed", file=sys.stderr)
    raise SystemExit(7)
if os.environ.get("FAKE_PIP_NOTICE"):
    print("[notice] A new release of pip is available", file=sys.stderr)
'''

_FAKE_AGENT = '''
import json
from pathlib import Path
with (Path.cwd() / "calls.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps("agent-stub") + "\\n")
'''


@unittest.skipUnless(os.name == "nt", "PowerShell launcher targets Windows")
class LauncherTests(unittest.TestCase):
    def test_batch_entry_starts_with_cmd_directive_without_bom(self):
        entry = Path(__file__).resolve().parents[1] / '安装并接入Agent.bat'
        self.assertTrue(entry.read_bytes().startswith(b'@echo off'), 'cmd.exe cannot parse a UTF-8 BOM')

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sf-launcher-")
        self.root = Path(self.temporary.name)
        self.powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        shutil.copy2(Path(__file__).resolve().parents[1] / "start.ps1", self.root / "start.ps1")
        (self.root / "requirements.txt").write_text("# synthetic requirements\n", encoding="utf-8")
        (self.root / "pip.py").write_text(_FAKE_PIP, encoding="utf-8")
        (self.root / "spiderfly_agent.py").write_text(_FAKE_AGENT, encoding="utf-8")
        # The real Python exists while dependencies have never completed: this
        # is precisely the failed-first-install state the launcher must recover.
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(self.root / ".venv")],
                       check=True, capture_output=True, timeout=30)
        self.marker = self.root / ".venv/.dependencies.sha256"

    def tearDown(self):
        self.temporary.cleanup()

    def launch(self, failure="", *, notice=False, wizard=False):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("SPIDERFLY_", "FEISHU_", "DEEPSEEK_"))
               and key not in {"PYTHONPATH", "PYTHONHOME", "PSModulePath"}}
        env["FAKE_PIP_FAIL_MODE"] = failure
        env["FAKE_PIP_NOTICE"] = "1" if notice else ""
        args = ["-File", str(self.root / "start.ps1"), "-DataDir", str(self.root / "synthetic-state")]
        if wizard:
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            # The nested powershell + merged output is the real installer topology.
            command = ("$ErrorActionPreference='Stop'; & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "
                       + quote(self.root/'start.ps1') + " -DataDir " + quote(self.root/'synthetic-state')
                       + " -PrepareOnly *>&1 | Out-File -Encoding utf8 -LiteralPath "
                       + quote(self.root/'wizard.log') + "; exit $LASTEXITCODE")
            args = ["-EncodedCommand", base64.b64encode(command.encode('utf-16le')).decode()]
        return subprocess.run([str(self.powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
                               "-ExecutionPolicy", "Bypass", *args],
                              cwd=self.root, env=env, capture_output=True, timeout=30)

    def calls(self):
        return [json.loads(line) for line in (self.root / "calls.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_install_failure_retries_on_next_start_with_existing_python(self):
        failed = self.launch("install")
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.calls(), ["install"])
        succeeded = self.launch("install")
        self.assertEqual(succeeded.returncode, 0, succeeded.stderr)
        self.assertEqual(self.calls(), ["install", "install", "check", "agent-stub"])
        self.assertTrue(self.marker.is_file())

    def test_check_failure_does_not_write_marker_and_retries(self):
        failed = self.launch("check")
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.calls(), ["install", "check"])
        succeeded = self.launch("check")
        self.assertEqual(succeeded.returncode, 0, succeeded.stderr)
        self.assertEqual(self.calls(), ["install", "check", "install", "check", "agent-stub"])

    def test_same_requirements_skip_install_but_changed_requirements_install(self):
        self.assertEqual(self.launch().returncode, 0)
        first_hash = self.marker.read_text(encoding="ascii")
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(self.calls(), ["install", "check", "agent-stub", "agent-stub"])
        (self.root / "requirements.txt").write_text("# changed synthetic requirements\n", encoding="utf-8")
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-3:], ["install", "check", "agent-stub"])
        expected = hashlib.sha256((self.root / "requirements.txt").read_bytes()).hexdigest().upper()
        self.assertEqual(self.marker.read_text(encoding="ascii"), expected)
        self.assertNotEqual(first_hash, expected)

    def test_wizard_redirected_stderr_notice_is_not_an_install_failure(self):
        result = self.launch(notice=True, wizard=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.is_file())
        self.assertEqual(self.calls(), ["install", "check"])

    def test_wizard_real_failure_keeps_reason_and_retries_without_cleaning(self):
        result = self.launch("install", notice=True, wizard=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())
        self.assertIn("synthetic dependency download failed", (self.root/'wizard.log').read_text(encoding='utf-8-sig'))
        result = self.launch("install", notice=True, wizard=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
