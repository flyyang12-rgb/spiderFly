"""Synthetic tests: no controller imports, real accounts, sites, or business files."""

import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import psutil

from spiderfly_agent.api import Api, ApiError, canonical
from spiderfly_agent.client import Client, recover
from spiderfly_agent.desktop import tray_label
from spiderfly_agent.identity import load_identity, public_key
from spiderfly_agent.journal import Journal
from spiderfly_agent.windows import SingleInstance
from spiderfly_agent.worker import Worker, package_hash, safe_artifact, validate_artifact_name


class TemporaryJournal(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="spiderfly-agent-test-")
        self.root = Path(self.temporary.name)
        self.journal = Journal(self.root / "journal.sqlite3")
        self.run_info = {"id": 1, "task_name": "synthetic", "timeout_seconds": 10,
                         "package_hash": package_hash("print('ok')", "")}

    def tearDown(self):
        self.journal.close()
        self.temporary.cleanup()


class JournalTests(TemporaryJournal):
    def test_duplicate_id_never_starts_twice_and_hash_conflict_is_rejected(self):
        self.assertTrue(self.journal.claim(self.run_info))
        self.assertFalse(self.journal.claim(self.run_info))
        with self.assertRaises(ValueError):
            self.journal.claim({**self.run_info, "package_hash": "f" * 64})
        with self.assertRaises(sqlite3.IntegrityError):
            self.journal.claim({**self.run_info, "id": 2})

    def test_restart_preserves_monotonic_sequence_and_ack(self):
        self.journal.claim(self.run_info)
        self.journal.append(1, "stdout", "first")
        self.journal.acknowledge(1, 1)
        self.journal.close()
        self.journal = Journal(self.root / "journal.sqlite3")
        self.assertEqual(self.journal.append(1, "stderr", "second"), 2)
        self.assertEqual([event["seq"] for event in self.journal.pending(1)], [2])
        self.journal.acknowledge(1, 0)
        self.assertEqual(self.journal.active()["ack_seq"], 1)
        with self.assertRaises(ValueError):
            self.journal.acknowledge(1, 3)

    def test_concurrent_output_is_contiguous(self):
        self.journal.claim(self.run_info)
        threads = [threading.Thread(target=lambda: [self.journal.append(1, "stdout", "x") for _ in range(20)]) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([event["seq"] for event in self.journal.pending(1)], list(range(1, 81)))

    def test_finished_is_persisted_once_after_artifacts_then_ack_releases(self):
        self.journal.claim(self.run_info)
        self.journal.add_artifact(1, "result.txt", self.root / "result.txt", "hash")
        self.journal.complete(1, "succeeded", "done", 0)
        self.journal.terminal_after_upload(1)
        self.assertEqual(self.journal.pending(1), [])
        self.journal.artifact_uploaded(1, "result.txt")
        self.journal.terminal_after_upload(1)
        self.journal.terminal_after_upload(1)
        self.assertEqual(len(self.journal.pending(1)), 1)
        self.assertEqual(self.journal.active()["state"], "terminal")
        self.journal.acknowledge(1, 1)
        self.assertIsNone(self.journal.active())
        self.assertFalse(self.journal.claim(self.run_info))

    def test_ambiguous_restart_holds_occupancy_and_event_is_not_duplicated(self):
        self.journal.claim(self.run_info)
        self.journal.set_process(1, state="spawning")
        recover(self.journal)
        recover(self.journal)
        self.assertTrue(self.journal.active()["recovery_required"])
        self.assertEqual([event["kind"] for event in self.journal.pending(1)], ["uncertain"])

    def test_restart_before_any_subprocess_fails_without_rerun(self):
        self.journal.claim(self.run_info)
        recover(self.journal)
        self.assertEqual(self.journal.active()["state"], "uploading")
        self.assertFalse(self.journal.claim(self.run_info))

    @unittest.skipUnless(os.name == "nt", "Windows job recovery")
    def test_restart_releases_only_recorded_job_whose_old_pid_is_gone(self):
        self.journal.claim(self.run_info)
        self.journal.set_process(1, 987654321, 1, job_owned=True)
        recover(self.journal)
        self.assertEqual(self.journal.active()["state"], "uploading")
        self.assertFalse(self.journal.active()["recovery_required"])


class TransportTests(unittest.TestCase):
    def test_signed_body_is_exact_bytes_retry_nonce_is_fresh(self):
        key = Ed25519PrivateKey.generate()
        api = Api("http://127.0.0.1:9356", key, 3)
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True}
        api.session.request = Mock(return_value=response)
        for _ in range(2):
            api.request("POST", "/api/agent/heartbeat", {"name": "测试"})
        calls = api.session.request.call_args_list
        first, second = calls[0].kwargs, calls[1].kwargs
        headers = first["headers"]
        key.public_key().verify(base64.b64decode(headers["X-SpiderFly-Signature"]), canonical(
            headers["X-SpiderFly-Time"], headers["X-SpiderFly-Nonce"], "POST", "/api/agent/heartbeat", first["data"]))
        self.assertNotEqual(headers["X-SpiderFly-Nonce"], second["headers"]["X-SpiderFly-Nonce"])
        self.assertFalse(first["allow_redirects"])
        self.assertFalse(api.session.trust_env)

    def test_redirect_is_not_followed(self):
        api = Api("http://127.0.0.1", Ed25519PrivateKey.generate(), 1)
        api.session.request = Mock(return_value=Mock(status_code=302))
        with self.assertRaises(ApiError):
            api.request("GET", "/api/agent/runs/1/package")

    def test_unicode_artifact_signs_decoded_path_and_original_bytes(self):
        from urllib.parse import quote
        key = Ed25519PrivateKey.generate()
        api = Api("http://127.0.0.1", key, 1)
        response = Mock(status_code=200)
        response.json.return_value = {}
        api.session.request = Mock(return_value=response)
        path = "/api/agent/runs/1/artifacts/结果.csv"
        api.request("POST", quote(path), raw=b"\x00\xff")
        request = api.session.request.call_args.kwargs
        headers = request["headers"]
        key.public_key().verify(base64.b64decode(headers["X-SpiderFly-Signature"]), canonical(
            headers["X-SpiderFly-Time"], headers["X-SpiderFly-Nonce"], "POST", path, b"\x00\xff"))

    def test_endpoint_cannot_have_credentials_or_foreign_path(self):
        for url in ("ftp://localhost", "http://user:secret@localhost", "http://localhost/api"):
            with self.assertRaises(ValueError):
                Api(url, Ed25519PrivateKey.generate())


class WorkerTests(TemporaryJournal):
    def make_worker(self, script: str, *, timeout=10, requirements=""):
        run = {**self.run_info, "timeout_seconds": timeout, "package_hash": package_hash(script, requirements)}
        self.journal.claim(run)
        api = Mock()
        api.request.return_value = {"script": script, "requirements": requirements, "package_hash": run["package_hash"]}
        worker = Worker(run, self.root, api, self.journal)
        worker.prepare_environment = Mock(return_value=Path(sys.executable))
        return worker

    def outcome(self):
        return json.loads(self.journal.active()["outcome"])

    def test_success_streams_both_channels_and_discovers_artifact(self):
        worker = self.make_worker("import os,sys\nfrom pathlib import Path\nprint('stdout')\nprint('stderr',file=sys.stderr)\nPath(os.environ['SPIDERFLY_ARTIFACT_DIR'],'result.txt').write_text('done')\n")
        worker.run()
        self.assertEqual(self.outcome()["status"], "succeeded")
        events = self.journal.pending(1)
        self.assertTrue(any(event["kind"] == "stdout" and "stdout" in event["text"] for event in events))
        self.assertTrue(any(event["kind"] == "stderr" and "stderr" in event["text"] for event in events))
        self.assertEqual(self.journal.pending_artifacts(1)[0]["name"], "result.txt")
        self.assertFalse(any(event["kind"] == "finished" for event in events))

    def test_nonzero_exit_is_failure(self):
        self.make_worker("raise SystemExit(7)\n").run()
        self.assertEqual(self.outcome()["status"], "failed")
        self.assertEqual(self.outcome()["exit_code"], 7)

    def test_timeout_terminates_owned_process(self):
        self.make_worker("import time\ntime.sleep(60)\n", timeout=1).run()
        self.assertEqual(self.outcome()["status"], "timed_out")

    def test_stop_during_preparation_applies_to_dependency_process(self):
        worker = self.make_worker("print('must not run')")
        worker.prepare_environment = lambda *args: worker.command([sys.executable, "-c", "import time;time.sleep(60)"])
        worker.start()
        time.sleep(0.4)
        worker.stop_event.set()
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.outcome()["status"], "cancelled")
        self.assertFalse(any(event["kind"] == "started" for event in self.journal.pending(1)))

    def test_hash_mismatch_never_executes(self):
        worker = self.make_worker("print('never')")
        worker.api.request.return_value["script"] = "print('modified')"
        worker.run()
        self.assertEqual(self.outcome()["status"], "failed")
        worker.prepare_environment.assert_not_called()

    def test_stop_before_download_does_not_fetch_package(self):
        worker = self.make_worker("print('never')")
        worker.stop_event.set()
        worker.run()
        self.assertEqual(self.outcome()["status"], "cancelled")
        worker.api.request.assert_not_called()
        worker.prepare_environment.assert_not_called()

    def test_package_stop_response_cancels_before_environment_preparation(self):
        worker = self.make_worker("print('never')")
        worker.api.request.return_value = {"stop_requested": True}
        worker.run()
        self.assertEqual(self.outcome()["status"], "cancelled")
        worker.prepare_environment.assert_not_called()
        self.assertFalse(any(event["kind"] == "started" for event in self.journal.pending(1)))

    def test_package_conflict_is_failure_not_cancellation(self):
        worker = self.make_worker("print('never')")
        worker.api.request.side_effect = ApiError(409, "conflict")
        worker.run()
        self.assertEqual(self.outcome()["status"], "failed")
        worker.prepare_environment.assert_not_called()

    def test_background_child_is_cleaned_after_parent_exit(self):
        worker = self.make_worker("import subprocess,sys,os\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\nPath(os.environ['SPIDERFLY_ARTIFACT_DIR'],'child.txt').write_text(str(p.pid))\n")
        worker.run()
        self.assertEqual(self.outcome()["status"], "succeeded")
        child_pid = int((worker.artifacts / "child.txt").read_text())
        self.assertFalse(psutil.pid_exists(child_pid))

    def test_environment_cache_reuses_ready_interpreter(self):
        worker = self.make_worker("print('ok')")
        cache = self.root / "envs" / worker.run_info["package_hash"][:32]
        python = cache / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir(parents=True)
        python.write_bytes(b"synthetic marker")
        (cache / ".ready").write_text(worker.run_info["package_hash"])
        worker.command = Mock(side_effect=AssertionError("cache must not install"))
        self.assertEqual(Worker.prepare_environment(worker, "", worker.run_info["package_hash"]), python)

    def test_dependency_install_failure_is_reported(self):
        worker = self.make_worker("print('ok')", requirements="missing-package")
        worker.work.mkdir(parents=True)
        worker.command = Mock(side_effect=[0, 1])
        with self.assertRaisesRegex(RuntimeError, "安装 Python 依赖失败"):
            Worker.prepare_environment(worker, "missing-package", worker.run_info["package_hash"])

    def test_real_standard_library_environment_prepares_and_executes(self):
        worker = self.make_worker("print('isolated-venv-ok')\n", timeout=40)
        worker.prepare_environment = lambda requirements, digest: Worker.prepare_environment(worker, requirements, digest)
        worker.run()
        self.assertEqual(self.outcome()["status"], "succeeded", self.outcome())
        cache = self.root / "envs" / worker.run_info["package_hash"][:32]
        self.assertTrue((cache / ".ready").is_file())
        self.assertTrue(any("isolated-venv-ok" in event["text"] for event in self.journal.pending(1)))

    def test_truncated_cache_hash_collision_is_rejected(self):
        worker = self.make_worker("print('ok')")
        cache = self.root / "envs" / worker.run_info["package_hash"][:32]
        cache.mkdir(parents=True)
        (cache / ".package-hash").write_text("different-full-digest")
        worker.command = Mock()
        with self.assertRaisesRegex(RuntimeError, "哈希前缀冲突"):
            Worker.prepare_environment(worker, "", worker.run_info["package_hash"])
        worker.command.assert_not_called()

    def test_unicode_casefold_artifact_collision_is_failed_not_uploaded_twice(self):
        worker = self.make_worker("import os\nfrom pathlib import Path\np=Path(os.environ['SPIDERFLY_ARTIFACT_DIR'])\n(p/'Straße.csv').write_text('a')\n(p/'STRASSE.csv').write_text('b')\n")
        worker.run()
        self.assertEqual(self.outcome()["status"], "failed")
        self.assertEqual(len(self.journal.pending_artifacts(1)), 1)


class LocalStateTests(unittest.TestCase):
    def test_tray_labels_cover_registration_and_dispatch_states(self):
        base = {"run_id": None, "connection": "connected", "interactive": True, "dispatch": False}
        self.assertEqual(tray_label({**base, "host_state": "pending"}), "等待管理员批准")
        self.assertEqual(tray_label({**base, "host_state": "non_dispatch"}), "非调度")
        self.assertEqual(tray_label({**base, "host_state": "idle", "dispatch": True}), "调度空闲")
        self.assertEqual(tray_label({**base, "run_id": 4, "task_name": "采集"}), "运行中 · 采集")
        self.assertEqual(tray_label({**base, "host_state": "offline", "connection": "error",
                                     "error": "Agent 已过旧，请人工升级"}), "需要升级 Agent")

    def test_invalid_artifact_names_match_controller_policy(self):
        for name in ("../escape", "name.", "name ", "NUL.txt", "COM1", "file:stream", "a" * 161):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_artifact_name(name)
        validate_artifact_name("结果.csv")

    def test_only_one_instance_per_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            key = f"SpiderFly.Agent.Test.{uuid.uuid4().hex}"
            with SingleInstance(Path(directory), key=key):
                with self.assertRaises(RuntimeError):
                    SingleInstance(Path(directory), key=key)

    def test_machine_lock_excludes_other_data_directories_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            key = f"SpiderFly.Agent.Test.{uuid.uuid4().hex}"
            first, second = Path(directory) / "first", Path(directory) / "second"
            with SingleInstance(first, key=key):
                with self.assertRaises(RuntimeError):
                    SingleInstance(second, key=key)
            with SingleInstance(second, key=key):
                pass

    @unittest.skipUnless(os.name == "nt", "Windows named mutex")
    def test_controller_compatible_named_mutex_excludes_agent(self):
        import ctypes
        from ctypes import wintypes
        key = f"SpiderFly.Agent.Test.{uuid.uuid4().hex}"
        name = "Global\\SpiderFly.SingleInstance." + hashlib.sha256(key.encode("utf-8")).hexdigest().upper()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateMutexW(None, False, name)
        self.assertTrue(handle)
        try:
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(RuntimeError, "旧版本机主控"):
                    SingleInstance(Path(directory), key=key)
        finally:
            kernel.CloseHandle(handle)

    @unittest.skipUnless(os.name == "nt", "DPAPI needs Windows")
    def test_identity_round_trip_is_stable_and_private_key_is_not_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            first = load_identity(Path(directory))
            second = load_identity(Path(directory))
            self.assertEqual(first[0], second[0])
            self.assertEqual(public_key(first[1]), public_key(second[1]))
            stored = json.loads((Path(directory) / "identity.json").read_text(encoding="utf-8"))
            self.assertGreater(len(base64.b64decode(stored["private_key"])), 32)

    def test_artifact_outside_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            outside = root / "outside.txt"
            outside.write_text("do not upload")
            with self.assertRaises(ValueError):
                safe_artifact(outside, artifact_dir)

    def test_symlinked_artifact_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            (outside / "private.txt").write_text("never upload")
            artifacts = root / "artifacts"
            try:
                artifacts.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("当前 Windows 账号不能创建符号链接")
            with self.assertRaises(ValueError):
                safe_artifact(artifacts / "private.txt", artifacts)


class ClientTests(TemporaryJournal):
    def test_local_shutdown_request_exits_without_heartbeat_and_is_consumed(self):
        request = self.root / "shutdown.request"
        request.write_text("stop", encoding="utf-8")
        api = Mock(host_id=1)
        client = Client(api, self.journal, self.root, shutdown_file=request)
        client.run()
        api.request.assert_not_called()
        self.assertFalse(request.exists())
        self.assertEqual(client.snapshot()["connection"], "stopped")

    def prepare_artifact(self, name="result.txt"):
        self.journal.claim(self.run_info)
        artifacts = self.root / "runs" / "1" / "artifacts"
        artifacts.mkdir(parents=True)
        path = artifacts / name
        path.write_bytes(b"result")
        self.journal.add_artifact(1, path.name, path, hashlib.sha256(b"result").hexdigest())
        self.journal.complete(1, "succeeded", "done", 0)

    def test_invalid_artifact_name_reaches_failed_terminal_without_network_upload(self):
        self.prepare_artifact("a" * 161)
        api = Mock(host_id=1)
        client = Client(api, self.journal, self.root)
        client.flush()
        api.request.assert_not_called()
        self.assertEqual(self.journal.active()["state"], "terminal")
        self.assertEqual(self.journal.pending(1)[-1]["status"], "failed")

    def test_permanent_artifact_rejection_does_not_strand_host(self):
        self.prepare_artifact()
        api = Mock(host_id=1)
        api.request.side_effect = [ApiError(413, "limit"), {"ack_seq": 2}]
        client = Client(api, self.journal, self.root)
        client.flush()
        self.assertEqual(self.journal.active()["state"], "terminal")
        self.assertEqual(self.journal.pending(1)[-1]["status"], "failed")
        client.flush()
        self.assertIsNone(self.journal.active())

    def test_transient_artifact_failure_still_sends_heartbeat_next_tick(self):
        self.prepare_artifact()
        api = Mock(host_id=1)
        def request(method, path, *args, **kwargs):
            if path == "/api/agent/heartbeat":
                return {"host": {"approval_status": "approved", "state": "running"}, "run": self.run_info}
            raise ApiError(503, "temporary")
        api.request.side_effect = request
        client = Client(api, self.journal, self.root, session_check=lambda: True)
        for _ in range(2):
            with self.assertRaises(ApiError):
                client.tick()
        self.assertEqual([call.args[1] for call in api.request.call_args_list].count("/api/agent/heartbeat"), 2)
        self.assertEqual(self.journal.active()["state"], "uploading")

    def test_non_dispatch_or_noninteractive_never_starts_task(self):
        api = Mock(host_id=1)
        api.request.return_value = {"host": {"approval_status": "approved", "state": "idle"}, "run": self.run_info}
        for dispatch, interactive in ((False, True), (True, False)):
            client = Client(api, self.journal, self.root, dispatch=dispatch, session_check=lambda: interactive)
            with patch("spiderfly_agent.client.Worker") as worker:
                client.tick()
                worker.assert_not_called()
        self.assertIsNone(self.journal.active())

    def test_running_host_cannot_disable_dispatch_without_stop_or_takeover(self):
        self.journal.claim(self.run_info)
        client = Client(Mock(host_id=1), self.journal, self.root, dispatch=True)
        self.assertFalse(client.set_dispatch(False))
        self.assertTrue(client.snapshot()["dispatch"])

    def test_emergency_takeover_stops_worker_then_disables_dispatch(self):
        client = Client(Mock(host_id=1), self.journal, self.root, dispatch=True)
        client.worker = Mock()
        client.worker.is_alive.return_value = True
        self.assertTrue(client.request_stop(emergency=True))
        client.worker.stop_event.set.assert_called_once()
        self.assertTrue(client.pending_dispatch_off)
        self.assertEqual(client.snapshot()["run_state"], "正在紧急接管")

    def test_structured_progress_is_reused_for_desktop_status(self):
        client = Client(Mock(host_id=1), self.journal, self.root)
        client.worker_event("stdout", 'SPIDERFLY_PROGRESS {"event":"progress","stage":"分页采集",')
        client.worker_event("stdout", '"page":2,"collected":18,"total":30}\n')
        self.assertEqual(client.snapshot()["progress"], "分页采集 · 第 2 页 · 已采集 18/30")

    def test_offer_is_persisted_before_claim_authorizes_worker(self):
        api = Mock(host_id=1, server="http://127.0.0.1", key=Ed25519PrivateKey.generate())
        api.request.side_effect = [
            {"host": {"approval_status": "approved", "state": "running"}, "run": self.run_info},
            self.run_info,
        ]
        client = Client(api, self.journal, self.root, dispatch=True, session_check=lambda: True)
        with patch("spiderfly_agent.client.Worker") as worker:
            client.heartbeat()
            worker.assert_called_once()
            worker.return_value.start.assert_called_once()
        self.assertEqual(api.request.call_args_list[1].args[:2],
                         ("POST", "/api/agent/runs/1/claim"))
        self.assertEqual(self.journal.active()["state"], "claimed")

    def test_lost_claim_response_keeps_durable_claim_and_retry_starts_once(self):
        api = Mock(host_id=1, server="http://127.0.0.1", key=Ed25519PrivateKey.generate())
        api.request.side_effect = [
            {"host": {"approval_status": "approved", "state": "running"}, "run": self.run_info},
            ApiError(503, "lost response"),
            {"host": {"approval_status": "approved", "state": "running"}, "run": self.run_info},
            self.run_info,
        ]
        client = Client(api, self.journal, self.root, dispatch=True, session_check=lambda: True)
        with patch("spiderfly_agent.client.Worker") as worker:
            with self.assertRaises(ApiError):
                client.heartbeat()
            self.assertEqual(self.journal.active()["state"], "claimed")
            worker.assert_not_called()
            client.heartbeat()
            worker.assert_called_once()

    def test_stop_during_claim_cancels_without_worker(self):
        api = Mock(host_id=1, server="http://127.0.0.1", key=Ed25519PrivateKey.generate())
        api.request.side_effect = [
            {"host": {"approval_status": "approved", "state": "stopping"}, "run": self.run_info},
            {"stop_requested": True},
        ]
        client = Client(api, self.journal, self.root, dispatch=True, session_check=lambda: True)
        with patch("spiderfly_agent.client.Worker") as worker:
            client.heartbeat()
            worker.assert_not_called()
        self.assertEqual(self.journal.active()["state"], "terminal")
        self.assertEqual(self.journal.pending(1)[-1]["status"], "cancelled")

    def test_artifact_upload_precedes_terminal_event_and_retry_is_idempotent(self):
        self.journal.claim(self.run_info)
        artifacts = self.root / "runs" / "1" / "artifacts"
        artifacts.mkdir(parents=True)
        path = artifacts / "result.txt"
        path.write_bytes(b"result")
        self.journal.add_artifact(1, path.name, path, hashlib.sha256(b"result").hexdigest())
        self.journal.complete(1, "succeeded", "done", 0)
        api = Mock(host_id=1)
        api.request.side_effect = [ApiError(503, "retry"), {}, {"ack_seq": 1}]
        client = Client(api, self.journal, self.root)
        with self.assertRaises(ApiError):
            client.flush()
        self.assertEqual(self.journal.pending(1), [])
        client.flush()
        self.assertEqual(self.journal.pending(1)[0]["kind"], "finished")
        client.flush()
        self.assertIsNone(self.journal.active())
        self.assertEqual(api.request.call_args_list[0].args[1], api.request.call_args_list[1].args[1])


if __name__ == "__main__":
    unittest.main()
