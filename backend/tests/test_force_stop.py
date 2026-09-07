from __future__ import annotations

import asyncio
import ctypes
import json
import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from starlette.requests import Request
from app import database, main, runner, security
from tests import test_app_api, test_member_management


class ForceStopTests(unittest.TestCase):
    request = test_member_management.MemberManagementTests.request

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.helper = test_app_api.ManagedAppApiTests()
        self.item = self.stack.enter_context(self.helper.fixture())
        self.task = self.helper.create_task(self.item["app_id"], self.item["user"])
        self.user = self.item["user"]
        self.token = security.create_session(self.user["id"])[0]
        self.script = self.item["app_dir"] / "main.py"
        self.work = database.DATA_DIR.parent / "work"
        self.stack.enter_context(patch.object(runner, "RPA_APPS_DIR", database.RPA_APPS_DIR))
        self.stack.enter_context(patch.object(runner, "RPA_ENVS_DIR", Path(sys.executable).resolve().parents[1]))
        self.stack.enter_context(patch.object(runner, "WORK_DIR", self.work))
        database.execute("UPDATE rpa_apps SET template_path='', template_filename='' WHERE id=?", (self.item["app_id"],))
        self.notify = self.stack.enter_context(patch.object(runner, "_send_notification", new=AsyncMock()))
        self.addCleanup(runner._execution_controls.clear)

    def execution(self, status="running", script=None):
        return database.execute("""INSERT INTO executions (task_id,status,trigger_source,requested_by,
            script_path_snapshot,python_path_snapshot,created_at) VALUES (?,?, 'schedule',?,?,?,?)""",
            (self.task["id"], status, self.user["id"], str(script or self.script), sys.executable, database.utc_now()))

    def test_api_permissions_idempotence_audit_and_detail(self):
        execution_id = self.execution()
        control = runner.ExecutionControl()
        runner._execution_controls[execution_id] = control
        url = f"/api/executions/{execution_id}/stop"
        self.assertEqual(self.request("POST", url)[0], 401)
        database.execute("UPDATE users SET role='operator' WHERE id=?", (self.user["id"],))
        self.assertEqual(self.request("POST", url, token=self.token)[0], 403)
        self.assertFalse(control.stop_event.is_set())
        for role in ("admin", "super_admin"):
            database.execute("UPDATE users SET role=? WHERE id=?", (role, self.user["id"]))
            self.assertEqual(self.request("POST", url, token=self.token)[0], 202)
        self.assertEqual(len(database.fetch_all("SELECT id FROM audit_logs WHERE action='force_stop_execution'")), 1)
        self.assertTrue(self.request("GET", f"/api/executions/{execution_id}", token=self.token)[1]["stop_requested"])
        self.assertEqual(database.fetch_one("SELECT status FROM executions WHERE id=?", (execution_id,))["status"], "running")

    def test_rejects_pending_finished_missing_or_unowned_run(self):
        pending = self.execution("pending")
        finished = self.execution("success")
        unowned = self.execution()
        for target, expected in [(pending,409),(finished,409),(unowned,409),(99999,404)]:
            self.assertEqual(self.request("POST", f"/api/executions/{target}/stop", token=self.token)[0], expected)
        self.assertEqual(database.fetch_one("SELECT status FROM executions WHERE id=?", (pending,))["status"], "pending")

    def test_stop_during_preparation_does_not_spawn_process(self):
        execution_id = self.execution()
        async def scenario():
            original = runner.prepare_work_directory
            def prepare(*args, **kwargs):
                # Simulate a stop arriving while the preparation thread is finishing.
                loop.call_soon_threadsafe(runner.request_execution_stop, execution_id, "tester")
                return original(*args, **kwargs)
            loop = asyncio.get_running_loop()
            with patch.object(runner, "prepare_work_directory", side_effect=prepare), patch.object(runner.asyncio, "create_subprocess_exec", new=AsyncMock()) as spawn:
                await runner.run_execution(execution_id)
                spawn.assert_not_awaited()
        asyncio.run(scenario())
        record = database.fetch_one("SELECT * FROM executions WHERE id=?", (execution_id,))
        self.assertEqual((record["status"], record["result_code"]), ("cancelled", "FORCE_STOPPED"))
        self.assertNotIn(execution_id, runner._execution_controls)
        self.notify.assert_awaited_once()

    def test_completed_wait_rejects_late_stop(self):
        async def scenario():
            control = runner.ExecutionControl()
            runner._execution_controls[99] = control
            process = unittest.mock.Mock(wait=AsyncMock(return_value=0))
            await runner._wait_for_process(process, control, 1)
            with self.assertRaises(ValueError):
                runner.request_execution_stop(99, "too-late")
        asyncio.run(scenario())

    def test_unconfirmed_termination_waits_before_releasing_execution(self):
        async def scenario():
            exited = asyncio.Event()
            process = unittest.mock.Mock(returncode=None)
            async def wait():
                await exited.wait()
                process.returncode = -1
            process.wait = wait
            with patch.object(runner, "_terminate_process", new=AsyncMock()), patch.object(runner, "append_execution_output"):
                stop = asyncio.create_task(runner._stop_process_confirmed(process, 999))
                await asyncio.sleep(0.05)
                self.assertFalse(stop.done())
                exited.set()
                await asyncio.wait_for(stop, 1)
        asyncio.run(scenario())

    @unittest.skipUnless(os.name == "nt", "Windows process-tree termination")
    def test_kills_hung_process_tree_preserves_logs_and_continues_queue(self):
        ready = self.item["app_dir"] / "ready.json"
        self.script.write_text("import os,sys,time,json,subprocess\nfrom pathlib import Path\nchild=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\nPath('ready.json').write_text(json.dumps([os.getpid(),child.pid]))\nprint('before-stop',flush=True)\nPath(os.environ['SPIDERFLY_WORK_DIR'],'unfinished.txt').write_text('partial')\ntime.sleep(60)\n", encoding="utf-8")
        next_script = self.item["app_dir"] / "next.py"
        next_script.write_text("print('next-task-completed')\n", encoding="utf-8")
        first = self.execution("pending")
        second = self.execution("pending", next_script)
        request = Request({"type":"http", "headers":[], "client":("127.0.0.1",1234)})
        async def wait_until(predicate, timeout=15):
            async def wait_loop():
                while not predicate():
                    await asyncio.sleep(0.02)
            await asyncio.wait_for(wait_loop(), timeout)
        async def scenario():
            with patch.object(main, "_host_waiting_reason", return_value=""):
                worker = asyncio.create_task(main._queue_worker_loop())
                try:
                    await wait_until(lambda: ready.exists() and 'before-stop' in database.fetch_one("SELECT stdout FROM executions WHERE id=?", (first,))["stdout"])
                    self.assertTrue((await main.force_stop_execution(first, request, self.user))["stop_requested"])
                    await wait_until(lambda: database.fetch_one("SELECT status FROM executions WHERE id=?", (second,))["status"] == "success" and self.notify.await_count == 2)
                finally:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
        asyncio.run(scenario())
        first_record = database.fetch_one("SELECT * FROM executions WHERE id=?", (first,))
        self.assertEqual((first_record["status"], first_record["result_code"]), ("cancelled", "FORCE_STOPPED"))
        self.assertIn("api-admin", first_record["error_message"])
        self.assertIn("before-stop", first_record["stdout"])
        self.assertIn("next-task-completed", database.fetch_one("SELECT stdout FROM executions WHERE id=?", (second,))["stdout"])
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertEqual(self.notify.await_count, 2)
        self.assertEqual(self.notify.await_args_list[0].args[2], "cancelled")
        for pid in json.loads(ready.read_text()):
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if handle:
                try:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 0), 0, f"process {pid} still running")
                finally:
                    kernel.CloseHandle(handle)
        self.assertFalse(runner._execution_controls)


if __name__ == "__main__":
    unittest.main()
