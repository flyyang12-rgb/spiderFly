from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


FLOW = Path(__file__).resolve().parents[2] / "flows/list_files.py"
CHILD = """
import importlib.abc, json, runpy, sys
from pathlib import Path
class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in {'app', 'example_flows', 'flows', 'examples'}:
            raise ImportError('Forbidden business or platform import: ' + fullname)
sys.meta_path.insert(0, Boundary())
from spiderfly_instructions import InstructionRegistry
actual, calls = InstructionRegistry.execute, []
def trace(registry, name, inputs=None):
    calls.append(name)
    return actual(registry, name, inputs)
InstructionRegistry.execute = trace
record = Path(sys.argv[1])
sys.argv = sys.argv[2:]
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
finally:
    record.write_text(json.dumps(calls), encoding='utf-8')
"""


class FileListFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / "输入目录"
        self.folder.mkdir()
        (self.folder / "一.xlsx").write_bytes(b"fixture: names only")
        (self.folder / "two.txt").write_bytes(b"untouched")
        self.sequence = 0

    def invoke(self, *, platform=False, missing=False):
        self.sequence += 1
        directory = self.root / str(self.sequence)
        entry_dir = directory / "entry"
        entry_dir.mkdir(parents=True)
        entry = entry_dir / "list_files.py"
        source = FLOW.read_text(encoding="utf-8")
        folder = self.folder / "missing" if missing else self.folder
        if platform:
            source = source.replace('FOLDER_PATH = ""', 'FOLDER_PATH = ' + repr(str(folder)))
            source = source.replace('FILE_PATTERN = "*"', 'FILE_PATTERN = "*.xlsx"')
        entry.write_text(source, encoding="utf-8")
        self.assertEqual(list(entry_dir.iterdir()), [entry])
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(("SPIDERFLY_", "FEISHU_"))
               and k.upper() not in ("PYTHONPATH", "PYTHONHOME")}
        if platform:
            (directory / "artifacts").mkdir()
            env.update(SPIDERFLY_RESULT_FILE=str(directory / "result.json"),
                       SPIDERFLY_ARTIFACT_DIR=str(directory / "artifacts"))
        trace = directory / "trace.json"
        result = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", "-c", CHILD, str(trace), str(entry),
             *([] if platform else [str(folder), "--pattern", "*.XLSX"])],
            cwd=entry_dir, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(json.loads(trace.read_text(encoding="utf-8")), ["file.list"])
        self.assertEqual((self.folder / "一.xlsx").read_bytes(), b"fixture: names only")
        self.assertEqual((self.folder / "two.txt").read_bytes(), b"untouched")
        return result, directory

    def test_local_single_file_flow_calls_installed_instruction(self):
        result, _ = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"files": [str(self.folder / "一.xlsx")], "count": 1})

    def test_platform_entry_saves_list_and_success_receipt_without_upload(self):
        result, directory = self.invoke(platform=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["outcome"], "success")
        output = directory / "artifacts/流程文件/输出/文件清单.json"
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {
            "files": [str(self.folder / "一.xlsx")], "count": 1,
        })

    def test_missing_folder_fails_in_cli_and_platform_without_fake_output(self):
        for platform in (False, True):
            with self.subTest(platform=platform):
                result, directory = self.invoke(platform=platform, missing=True)
                self.assertEqual(result.returncode, 1)
                if platform:
                    receipt = json.loads((directory / "result.json").read_text(encoding="utf-8"))
                    self.assertEqual(receipt["outcome"], "failure")
                    self.assertEqual(receipt["code"], "FILE_FOLDER_NOT_FOUND")
                    self.assertEqual(list((directory / "artifacts/流程文件/输出").iterdir()), [])
                else:
                    self.assertEqual(json.loads(result.stderr)["code"], "FILE_FOLDER_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
