"""Isolated Windows launcher checks; never starts the real Agent or touches its data."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt", "Windows PowerShell management only")
class ManagementScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="spiderfly agent management ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.agent = self.root / "Agent with spaces"
        self.agent.mkdir()
        self.data = self.root / "State with spaces"
        self.data.mkdir()
        shutil.copy2(Path(__file__).resolve().parents[1] / "manage.ps1", self.agent / "manage.ps1")
        venv = self.agent / ".venv"
        (venv / "Scripts").mkdir(parents=True)
        (venv / "Scripts" / "python.exe").touch()
        base_python = sys._base_executable
        (venv / "pyvenv.cfg").write_text(f"executable = {base_python}\n", encoding="utf-8")
        module = self.root / "spiderfly_agent"
        module.mkdir()
        (module / "__main__.py").write_text(
            "import json, os, pathlib, sys, time\n"
            "data = pathlib.Path(sys.argv[sys.argv.index('--data-dir') + 1])\n"
            "(data / 'agent.pid.json').write_text(json.dumps({'pid': os.getpid(), 'version': 'test', 'protocol_version': 1}))\n"
            "try:\n"
            "    while not (data / 'shutdown.request').exists(): time.sleep(0.1)\n"
            "finally:\n"
            "    (data / 'agent.pid.json').unlink(missing_ok=True)\n",
            encoding="utf-8",
        )
        literal = "'" + sys.executable.replace("'", "''") + "'"
        (self.agent / "start.ps1").write_text(
            "param([string]$DataDir, [string]$Server, [string]$Code, [string]$Name, [switch]$Dispatch)\n"
            f"& {literal} -m spiderfly_agent --data-dir $DataDir run\n",
            encoding="utf-8-sig",
        )
        self.environment = os.environ.copy()
        self.environment["PYTHONPATH"] = str(self.root)

    def manage(self, action, *extra):
        out_path, err_path = self.root / "command.out", self.root / "command.err"
        with out_path.open("wb") as output, err_path.open("wb") as errors:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(self.agent / "manage.ps1"), action, "-DataDir", str(self.data), *extra],
                stdout=output, stderr=errors, env=self.environment, cwd=self.root, timeout=25,
            )
        return subprocess.CompletedProcess(
            result.args, result.returncode,
            out_path.read_text(encoding="utf-8", errors="replace"),
            err_path.read_text(encoding="utf-8", errors="replace"),
        )

    def test_start_status_and_stop_accept_base_python_process_and_space_paths(self):
        try:
            started = self.manage("Start", "-Name", "Office PC with spaces")
            error_log = self.data / "logs/agent.error.log"
            details = error_log.read_text(encoding="utf-8", errors="replace") if error_log.exists() else "no child error log"
            self.assertEqual(started.returncode, 0, started.stderr + "\n" + details)
            self.assertIn("PID", started.stdout)
            record = json.loads((self.data / "agent.pid.json").read_text(encoding="utf-8"))
            self.assertNotEqual(Path(sys.executable).resolve(), (self.agent / ".venv/Scripts/python.exe").resolve())
            self.assertGreater(record["pid"], 0)
            status = self.manage("Status")
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn("PID", status.stdout)
            stopped = self.manage("Stop")
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertFalse((self.data / "agent.pid.json").exists())
        finally:
            if (self.data / "agent.pid.json").exists():
                self.manage("Stop")

    def test_upgrade_includes_installation_wizard(self):
        package = self.root / "New package"
        package.mkdir()
        (self.agent / "README.md").write_text("old", encoding="utf-8")
        (self.agent / "requirements.txt").write_text("old", encoding="utf-8")
        (self.agent / "spiderfly_agent").mkdir()
        (self.agent / "spiderfly_agent/__init__.py").write_text("old", encoding="utf-8")
        for name in ("README.md", "requirements.txt", "start.ps1", "manage.ps1",
                     "setup.ps1", "install-python.ps1", "安装并接入Agent.bat"):
            shutil.copy2(self.agent / name, package / name) if (self.agent / name).exists() else (
                package / name).write_text("new", encoding="utf-8")
        (package / "spiderfly_agent").mkdir()
        (package / "spiderfly_agent/__init__.py").write_text("new", encoding="utf-8")
        upgraded = self.manage("Upgrade", "-PackagePath", str(package))
        self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
        for name in ("setup.ps1", "install-python.ps1", "安装并接入Agent.bat"):
            self.assertTrue((self.agent / name).is_file(), name)
        self.assertEqual((self.agent / "spiderfly_agent/__init__.py").read_text(encoding="utf-8"), "new")
        self.assertTrue(list(self.root.glob("SpiderFlyAgent-program-*.zip")))

    def test_unrelated_pid_is_never_treated_as_agent(self):
        unrelated = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(15)"],
                                     cwd=self.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            (self.data / "agent.pid.json").write_text(json.dumps({"pid": unrelated.pid}), encoding="utf-8")
            status = self.manage("Status")
            self.assertNotEqual(status.returncode, 0)
            self.assertFalse((self.data / "shutdown.request").exists())
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
