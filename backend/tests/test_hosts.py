"""Agent v1 HTTP contract checks; run only from an isolated source copy.

The tests use synthetic tasks, fresh SQLite databases and real Ed25519 signatures.
ASGITransport deliberately does not run the application lifespan or background workers.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import tempfile
import time
import unittest
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import database, main, maintenance, remote_maintenance, security
from app.api import hosts as hosts_api
from app.services import host_dispatch


class HostApiTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for attribute, value in {
            "DATA_DIR": self.root,
            "DB_PATH": self.root / "test.db",
            "RPA_APPS_DIR": self.root / "apps",
            "RPA_ENVS_DIR": self.root / "envs",
        }.items():
            self.stack.enter_context(patch.object(database, attribute, value))
        self.stack.enter_context(patch.object(host_dispatch, "DATA_DIR", self.root))
        self.stack.enter_context(patch.object(maintenance, "RPA_APPS_DIR", self.root / "apps"))
        database.init_db()
        host_dispatch.init_tables()
        maintenance.init_tables()
        now = database.utc_now()
        self.tokens = {}
        for username, role in (("admin", "admin"), ("member", "operator")):
            user_id = database.execute(
                """INSERT INTO users
                (username,display_name,password_hash,role,created_at,updated_at)
                VALUES (?,?,'unused',?,?,?)""",
                (username, username, role, now, now),
            )
            self.tokens[username] = security.create_session(user_id)[0]
        self.keys = {}
        self.task_id, self.script_path, self.app_id = self.make_task()

    def make_task(self, source="print('synthetic task')\n", requirements=""):
        name = "合成任务 " + uuid.uuid4().hex[:10]
        path = self.root / "apps" / (uuid.uuid4().hex + ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8", newline="")
        now = database.utc_now()
        app_id = database.execute(
            """INSERT INTO rpa_apps
            (name,script_path,requirements_text,environment_status,created_at,updated_at)
            VALUES (?,?,?,'ready',?,?)""",
            (name, str(path), requirements, now, now),
        )
        task_id = database.execute(
            """INSERT INTO tasks
            (name,app_id,script_path,notify_on_success,notify_on_failure,created_at,updated_at)
            VALUES (?,?,?,0,0,?,?)""",
            (name, app_id, str(path), now, now),
        )
        return task_id, path, app_id

    def request(self, method, path, body=None, *, raw=None, user="admin", headers=None, origin=True):
        actual_headers = {}
        if user is not None and method in {"POST", "PATCH", "PUT", "DELETE"}:
            actual_headers["Content-Type"] = "application/json"
        if body is not None:
            raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            actual_headers["Content-Type"] = "application/json"
        if user is not None:
            actual_headers["Cookie"] = f"{security.SESSION_COOKIE_NAME}={self.tokens[user]}"
        if origin:
            actual_headers["Origin"] = "http://testserver"
        actual_headers.update(headers or {})

        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://testserver"
            ) as client:
                return await client.request(method, path, content=raw or b"", headers=actual_headers)

        return asyncio.run(send())

    def success(self, response):
        self.assertIn(response.status_code, (200, 201, 202), response.text)
        return response.json()

    def new_code(self):
        return self.success(self.request("POST", "/api/hosts/enrollment-codes"))["code"]

    def registration(self, code=None, machine_id=None, key=None):
        private_key = key or Ed25519PrivateKey.generate()
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        payload = {
            "enrollment_code": code or self.new_code(),
            "machine_id": machine_id or uuid.uuid4().hex,
            "name": "合成 Windows 宿主机",
            "public_key": base64.b64encode(public_bytes).decode("ascii"),
            "agent_version": "0.3.0",
            "protocol_version": 1,
        }
        return payload, private_key

    def register(self, *, approved=True, enabled=True):
        payload, key = self.registration()
        result = self.success(self.request("POST", "/api/agent/register", payload, user=None))
        host_id = result["host_id"]
        self.keys[host_id] = key
        if approved:
            self.success(self.request("POST", f"/api/hosts/{host_id}/approve"))
            if enabled:
                self.success(self.request("PATCH", f"/api/hosts/{host_id}/mode", {"dispatch_enabled": True}))
            self.heartbeat(host_id)
        return host_id

    def signed_headers(self, host_id, method, path, raw=b"", *, key=None, timestamp=None, nonce=None):
        timestamp = str(int(time.time()) if timestamp is None else timestamp)
        nonce = nonce or uuid.uuid4().hex
        canonical = "\n".join((timestamp, nonce, method.upper(), unquote(path), hashlib.sha256(raw).hexdigest()))
        signature = (key or self.keys[host_id]).sign(canonical.encode("utf-8"))
        return {
            "X-SpiderFly-Host": str(host_id),
            "X-SpiderFly-Time": timestamp,
            "X-SpiderFly-Nonce": nonce,
            "X-SpiderFly-Signature": base64.b64encode(signature).decode("ascii"),
        }

    def agent(self, host_id, method, path, body=None, *, raw=None, **signature_options):
        headers = {}
        if body is not None:
            raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        raw = raw or b""
        headers.update(self.signed_headers(host_id, method, path, raw, **signature_options))
        return self.request(method, path, raw=raw, headers=headers, user=None, origin=False)

    def heartbeat(self, host_id, **changes):
        payload = {
            "protocol_version": 1,
            "agent_version": "0.3.0",
            "dispatch_enabled": True,
            "interactive_session": True,
            "active_run_id": None,
            "recovery_required": False,
            **changes,
        }
        return self.success(self.agent(host_id, "POST", "/api/agent/heartbeat", payload))

    def queue(self, host_id, *, task_id=None, request_id=None):
        return self.success(self.request("POST", f"/api/hosts/{host_id}/runs", {
            "task_id": task_id or self.task_id,
            "request_id": request_id or str(uuid.uuid4()),
        }))

    def leased(self):
        host_id = self.register()
        run = self.queue(host_id)
        self.assertEqual(self.heartbeat(host_id)["run"]["id"], run["id"])
        self.success(self.agent(host_id, "POST", f"/api/agent/runs/{run['id']}/claim"))
        return host_id, run

    def events(self, host_id, run_id, *events):
        return self.agent(host_id, "POST", f"/api/agent/runs/{run_id}/events", {"events": list(events)})

    def detail(self, run_id):
        return self.success(self.request("GET", f"/api/remote-runs/{run_id}"))

    def host(self, host_id):
        return next(item for item in self.success(self.request("GET", "/api/hosts")) if item["id"] == host_id)

    def test_registration_code_is_consumed_and_same_identity_retry_is_idempotent(self):
        payload, _ = self.registration()
        first = self.success(self.request("POST", "/api/agent/register", payload, user=None))
        retry = self.success(self.request("POST", "/api/agent/register", payload, user=None))
        self.assertEqual(first, retry)
        self.assertEqual(first["approval_status"], "pending")
        self.assertEqual(first["protocol_version"], 1)
        second_payload, _ = self.registration(code=payload["enrollment_code"])
        self.assertIn(self.request("POST", "/api/agent/register", second_payload, user=None).status_code, (400, 403, 409))
        self.assertEqual(len(self.success(self.request("GET", "/api/hosts"))), 1)

    def test_incompatible_registration_is_rejected_without_consuming_code(self):
        payload, _ = self.registration()
        payload["agent_version"] = "0.2.0"
        response = self.request("POST", "/api/agent/register", payload, user=None)
        self.assertEqual(response.status_code, 409)
        self.assertIn("最低支持 0.3.0", response.json()["detail"])
        payload["agent_version"] = "0.3.0"
        result = self.success(self.request("POST", "/api/agent/register", payload, user=None))
        self.assertEqual(result["minimum_agent_version"], "0.3.0")
        self.assertEqual(result["current_agent_version"], "0.3.0")

    def test_incompatible_heartbeat_never_receives_queued_run(self):
        host_id = self.register()
        run = self.queue(host_id)
        response = self.agent(host_id, "POST", "/api/agent/heartbeat", {
            "protocol_version": 1, "agent_version": "0.2.0", "dispatch_enabled": True,
            "interactive_session": True, "active_run_id": None, "recovery_required": False,
        })
        self.assertEqual(response.status_code, 409)
        self.assertIn("人工升级", response.json()["detail"])
        self.assertEqual(self.detail(run["id"])["status"], "queued")

    def test_existing_machine_id_cannot_be_rebound_to_another_key(self):
        payload, _ = self.registration()
        original = self.success(self.request("POST", "/api/agent/register", payload, user=None))
        changed, _ = self.registration(machine_id=payload["machine_id"])
        self.assertEqual(self.request("POST", "/api/agent/register", changed, user=None).status_code, 409)
        self.assertEqual(len(self.success(self.request("GET", "/api/hosts"))), 1)
        self.assertEqual(self.host(original["host_id"])["approval_status"], "pending")

    def test_expired_enrollment_code_cannot_register(self):
        payload, _ = self.registration()
        database.execute("UPDATE agent_enrollment_codes SET expires_at = ?", (time.time() - 1,))
        self.assertIn(self.request("POST", "/api/agent/register", payload, user=None).status_code, (400, 403, 410))
        self.assertEqual(self.success(self.request("GET", "/api/hosts")), [])

    def test_pending_host_can_report_state_but_cannot_receive_tasks(self):
        host_id = self.register(approved=False)
        heartbeat = self.heartbeat(host_id)
        self.assertIsNone(heartbeat["run"])
        self.assertEqual(heartbeat["host"]["approval_status"], "pending")
        response = self.request("POST", f"/api/hosts/{host_id}/runs", {
            "task_id": self.task_id, "request_id": str(uuid.uuid4()),
        })
        self.assertIn(response.status_code, (400, 403, 409))

    def test_revoked_or_rejected_identity_cannot_call_agent_endpoints(self):
        for action, approved in (("revoke", True), ("reject", False)):
            with self.subTest(action=action):
                host_id = self.register(approved=approved)
                result = self.success(self.request("POST", f"/api/hosts/{host_id}/{action}"))
                self.assertEqual(result["approval_status"], "revoked" if approved else "rejected")
                response = self.agent(host_id, "POST", "/api/agent/heartbeat", {
                    "protocol_version": 1, "agent_version": "0.3.0", "dispatch_enabled": True,
                    "interactive_session": True, "active_run_id": None, "recovery_required": False,
                })
                self.assertIn(response.status_code, (401, 403))

    def test_signature_rejects_foreign_key_old_timestamp_and_replayed_nonce(self):
        host_id = self.register()
        path = "/api/agent/heartbeat"
        body = {"protocol_version": 1, "agent_version": "0.3.0", "dispatch_enabled": False,
                "interactive_session": True, "active_run_id": None, "recovery_required": False}
        for changes in ({"key": Ed25519PrivateKey.generate()}, {"timestamp": int(time.time()) - 120},
                        {"timestamp": int(time.time()) + 120}):
            with self.subTest(changes=list(changes)):
                self.assertIn(self.agent(host_id, "POST", path, body, **changes).status_code, (401, 403))
        nonce = uuid.uuid4().hex
        self.success(self.agent(host_id, "POST", path, body, nonce=nonce))
        self.assertIn(self.agent(host_id, "POST", path, body, nonce=nonce).status_code, (401, 403, 409))

    def test_signature_covers_raw_body_and_cookie_cannot_replace_machine_identity(self):
        host_id = self.register()
        path = "/api/agent/heartbeat"
        original = b'{"protocol_version":1,"dispatch_enabled":false}'
        headers = self.signed_headers(host_id, "POST", path, original)
        headers["Content-Type"] = "application/json"
        tampered = original.replace(b"false", b"true")
        self.assertIn(self.request("POST", path, raw=tampered, headers=headers, user=None).status_code, (401, 403))
        self.assertIn(self.request("POST", path, raw=original, headers={"Content-Type": "application/json"}).status_code, (401, 403))

    def test_admin_writes_require_same_origin_and_member_cannot_manage_hosts(self):
        host_id = self.register(approved=False)
        writes = [
            ("POST", "/api/hosts/enrollment-codes", None),
            ("POST", f"/api/hosts/{host_id}/approve", None),
            ("POST", f"/api/hosts/{host_id}/reject", None),
            ("POST", f"/api/hosts/{host_id}/revoke", None),
            ("PATCH", f"/api/hosts/{host_id}/mode", {"dispatch_enabled": True}),
            ("POST", f"/api/hosts/{host_id}/runs", {"task_id": self.task_id, "request_id": str(uuid.uuid4())}),
        ]
        for method, path, body in writes:
            with self.subTest(path=path):
                self.assertEqual(self.request(method, path, body, user="member").status_code, 403)
                self.assertEqual(self.request(method, path, body, headers={"Origin": "https://foreign.invalid"}).status_code, 403)
        self.assertEqual(self.request("POST", "/api/hosts/enrollment-codes", origin=False).status_code, 403)
        self.assertEqual(self.request("GET", "/api/hosts", user=None).status_code, 401)

    def test_origin_scheme_and_port_are_part_of_same_origin_check(self):
        for origin in ("https://testserver", "http://testserver:99", "http://testserver.evil.invalid", "null"):
            with self.subTest(origin=origin):
                response = self.request("POST", "/api/hosts/enrollment-codes", headers={"Origin": origin})
                self.assertEqual(response.status_code, 403)

    def test_bodyless_admin_writes_reject_form_and_plain_text_content_types(self):
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            with self.subTest(content_type=content_type):
                response = self.request("POST", "/api/hosts/enrollment-codes", headers={"Content-Type": content_type})
                self.assertEqual(response.status_code, 415)

    def test_revocation_between_signature_check_and_heartbeat_prevents_state_update(self):
        host_id = self.register()
        authenticate = host_dispatch.authenticate
        actor = database.fetch_one("SELECT * FROM users WHERE username='admin'")

        def revoke_after_authentication(*args, **kwargs):
            verified_id = authenticate(*args, **kwargs)
            host_dispatch.change_approval(verified_id, "revoke", actor)
            return verified_id

        with patch.object(host_dispatch, "authenticate", side_effect=revoke_after_authentication):
            response = self.agent(host_id, "POST", "/api/agent/heartbeat", {
                "protocol_version": 1, "agent_version": "revoked-must-not-write",
                "dispatch_enabled": True, "interactive_session": True,
                "active_run_id": None, "recovery_required": False,
            })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.host(host_id)["agent_version"], "0.3.0")

    def test_admin_actions_are_audited_without_registration_secrets(self):
        payload, _ = self.registration()
        result = self.success(self.request("POST", "/api/agent/register", payload, user=None))
        self.success(self.request("POST", f"/api/hosts/{result['host_id']}/approve"))
        records = database.fetch_all("SELECT action,summary FROM audit_logs")
        self.assertGreaterEqual(len(records), 2)
        serialized = json.dumps(records)
        self.assertNotIn(payload["enrollment_code"], serialized)
        self.assertNotIn(payload["public_key"], serialized)

    def make_agent_sources(self, missing=None):
        root = self.root / ("agent-source-" + uuid.uuid4().hex)
        modules = ("__init__", "__main__", "api", "client", "desktop", "identity", "journal", "windows", "worker")
        sources = {"README.md": "Synthetic Agent instructions\n", "requirements.txt": "cryptography\n",
                   "start.ps1": "# synthetic launcher\n", "manage.ps1": "# synthetic operations\n",
                   "setup.ps1": "# synthetic setup wizard\n", "安装并接入Agent.bat": "@echo off\n"}
        sources.update({f"spiderfly_agent/{name}.py": f"# synthetic {name}\n" for name in modules})
        for relative, content in sources.items():
            if relative == missing:
                continue
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="")
        for relative in (
            "identity.json", "config.json", "journal.sqlite3", ".env", "data/identity.json",
            "spiderfly_agent/credentials.json", "spiderfly_agent/local_secret_dump.py",
            "spiderfly_agent/__pycache__/identity.pyc", ".venv/Lib/site-packages/private.py",
            "tests/test_agent.py", "runs/1/artifacts/private.csv",
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("SYNTHETIC_LOCAL_SECRET_DO_NOT_PACKAGE", encoding="utf-8")
        return root, sources

    def test_admin_download_contains_only_complete_agent_sources_and_no_local_secrets(self):
        root, sources = self.make_agent_sources()
        with patch.object(hosts_api, "AGENT_ROOT", root):
            response = self.request("GET", "/api/hosts/agent-download")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("SpiderFlyAgent-0.3.0.zip", response.headers["content-disposition"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(set(archive.namelist()), {"SpiderFlyAgent/" + name for name in sources})
            for name, source in sources.items():
                self.assertEqual(archive.read("SpiderFlyAgent/" + name), source.encode("utf-8"))
            self.assertTrue(all(b"SYNTHETIC_LOCAL_SECRET" not in archive.read(name) for name in archive.namelist()))

    def test_agent_download_requires_administrator_session(self):
        for user, expected in ((None, 401), ("member", 403)):
            with self.subTest(user=user), patch.object(hosts_api, "AGENT_ROOT", self.root / "missing"):
                self.assertEqual(self.request("GET", "/api/hosts/agent-download", user=user).status_code, expected)

    def test_agent_download_refuses_incomplete_launcher_or_module_package(self):
        for missing in ("start.ps1", "manage.ps1", "setup.ps1", "安装并接入Agent.bat",
                        "spiderfly_agent/worker.py", "spiderfly_agent/desktop.py"):
            with self.subTest(missing=missing):
                root, _ = self.make_agent_sources(missing=missing)
                with patch.object(hosts_api, "AGENT_ROOT", root):
                    response = self.request("GET", "/api/hosts/agent-download")
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("SYNTHETIC_LOCAL_SECRET", response.text)

    def test_both_dispatch_switches_and_interactive_session_gate_claims(self):
        host_id = self.register(enabled=False)
        run = self.queue(host_id)
        self.assertIsNone(self.heartbeat(host_id)["run"])
        self.success(self.request("PATCH", f"/api/hosts/{host_id}/mode", {"dispatch_enabled": True}))
        self.assertIsNone(self.heartbeat(host_id, dispatch_enabled=False)["run"])
        self.assertIsNone(self.heartbeat(host_id, interactive_session=False)["run"])
        self.assertEqual(self.detail(run["id"])["status"], "queued")
        self.assertEqual(self.heartbeat(host_id)["run"]["id"], run["id"])

    def test_offline_target_stays_queued_without_switching_to_another_host(self):
        host_id = self.register()
        other_id = self.register()
        stale = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        database.execute("UPDATE agent_hosts SET last_seen_at=? WHERE id=?", (stale, host_id))
        run = self.queue(host_id)
        self.assertIsNone(self.heartbeat(other_id)["run"])
        self.assertEqual(self.detail(run["id"])["host_id"], host_id)
        self.assertEqual(self.detail(run["id"])["status"], "queued")
        self.assertFalse(self.host(host_id)["online"])
        self.assertEqual(self.heartbeat(host_id)["run"]["id"], run["id"])

    def test_request_id_deduplication_and_conflicting_task_or_host(self):
        host_id = self.register()
        other_id = self.register()
        other_task, _, _ = self.make_task()
        request_id = str(uuid.uuid4())
        first = self.queue(host_id, request_id=request_id)
        self.assertEqual(self.queue(host_id, request_id=request_id)["id"], first["id"])
        for target, task in ((other_id, self.task_id), (host_id, other_task)):
            response = self.request("POST", f"/api/hosts/{target}/runs", {"task_id": task, "request_id": request_id})
            self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.success(self.request("GET", "/api/remote-runs"))), 1)

    def test_concurrent_retries_create_only_one_run(self):
        host_id = self.register()
        request_id = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.queue(host_id, request_id=request_id), range(8)))
        self.assertEqual(len({item["id"] for item in results}), 1)
        self.assertEqual(len(self.success(self.request("GET", "/api/remote-runs"))), 1)

    def test_concurrent_heartbeats_cannot_claim_two_runs(self):
        host_id = self.register()
        first = self.queue(host_id)
        second = self.queue(host_id)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.heartbeat(host_id), range(4)))
        claimed_ids = {(item["run"] or {}).get("id") for item in results}
        self.assertNotIn(second["id"], claimed_ids)
        self.assertIn(first["id"], claimed_ids)
        self.assertEqual(self.host(host_id)["active_run_id"], first["id"])
        self.assertEqual(self.detail(second["id"])["status"], "queued")

    def test_first_increment_rejects_template_inputs_and_non_native_runtime(self):
        host_id = self.register()
        database.execute("UPDATE rpa_apps SET template_path=? WHERE id=?", (str(self.root / "input.xlsx"), self.app_id))
        payload = {"task_id": self.task_id, "request_id": str(uuid.uuid4())}
        self.assertIn(self.request("POST", f"/api/hosts/{host_id}/runs", payload).status_code, (400, 409))
        database.execute("UPDATE rpa_apps SET template_path='' WHERE id=?", (self.app_id,))
        maintenance.init_tables()
        database.execute(
            "INSERT INTO maintenance_policies(task_id,owner_id,runtime,updated_at) VALUES (?,0,'readonly-v1',?)",
            (self.task_id, database.utc_now()),
        )
        for runtime in ("readonly-v1", "collection-v1", "drissionpage-v1"):
            with self.subTest(runtime=runtime):
                database.execute("UPDATE maintenance_policies SET runtime=? WHERE task_id=?", (runtime, self.task_id))
                self.assertIn(self.request("POST", f"/api/hosts/{host_id}/runs", payload).status_code, (400, 409))
        self.assertEqual(self.success(self.request("GET", "/api/remote-runs")), [])

    def test_queue_does_not_preexecute_or_syntax_validate_the_uploaded_script(self):
        host_id = self.register()
        self.script_path.write_text("print(\n", encoding="utf-8", newline="")
        run = self.queue(host_id)
        self.assertEqual(run["status"], "queued")
        self.heartbeat(host_id)
        self.success(self.agent(host_id, "POST", f"/api/agent/runs/{run['id']}/claim"))
        package = self.success(self.agent(host_id, "GET", f"/api/agent/runs/{run['id']}/package"))
        self.assertEqual(package["script"], "print(\n")

    def test_frozen_source_and_requirements_survive_task_changes(self):
        original = self.script_path.read_bytes().decode("utf-8")
        requirements = "sample-package==1.2.3\n"
        database.execute("UPDATE rpa_apps SET requirements_text=? WHERE id=?", (requirements, self.app_id))
        host_id = self.register()
        request_id = str(uuid.uuid4())
        run = self.queue(host_id, request_id=request_id)
        self.script_path.write_text("raise RuntimeError('changed')\n", encoding="utf-8")
        database.execute("UPDATE rpa_apps SET requirements_text='' WHERE id=?", (self.app_id,))
        self.assertEqual(self.queue(host_id, request_id=request_id)["id"], run["id"])
        self.heartbeat(host_id)
        self.success(self.agent(host_id, "POST", f"/api/agent/runs/{run['id']}/claim"))
        package = self.success(self.agent(host_id, "GET", f"/api/agent/runs/{run['id']}/package"))
        self.assertEqual(package["script"], original)
        self.assertEqual(package["requirements"], requirements)
        expected = hashlib.sha256(original.encode("utf-8") + b"\0" + requirements.encode("utf-8")).hexdigest()
        self.assertEqual(package["package_hash"], expected)
        self.assertEqual(run["package_hash"], expected)
        detail = self.detail(run["id"])
        self.assertEqual(detail["version_sequence"], 1)
        self.assertIsInstance(detail["code_version_id"], int)
        version = database.fetch_one("SELECT * FROM task_code_versions WHERE id=?", (detail["code_version_id"],))
        self.assertEqual((version["source"], version["requirements"]), (original, requirements))

    def test_remote_run_is_bound_to_immutable_version_sequence(self):
        host_id = self.register()
        first = self.queue(host_id)
        self.assertEqual(first["version_sequence"], 1)
        self.script_path.write_text("print('v2')\n", encoding="utf-8")
        second = self.queue(host_id)
        self.assertEqual(second["version_sequence"], 2)
        self.assertNotEqual(first["code_version_id"], second["code_version_id"])
        self.assertEqual(self.detail(first["id"])["package_hash"], first["package_hash"])
        self.assertEqual(database.fetch_one(
            "SELECT source FROM task_code_versions WHERE id=?", (first["code_version_id"],)
        )["source"], "print('synthetic task')\n")

    def test_only_assigned_host_can_download_leased_package_or_write_events(self):
        host_id = self.register()
        other_id = self.register()
        run = self.queue(host_id)
        package_path = f"/api/agent/runs/{run['id']}/package"
        self.assertIn(self.agent(host_id, "GET", package_path).status_code, (403, 409))
        self.heartbeat(host_id)
        self.success(self.agent(host_id, "POST", f"/api/agent/runs/{run['id']}/claim"))
        self.success(self.agent(host_id, "GET", package_path))
        self.assertIn(self.agent(other_id, "GET", package_path).status_code, (403, 404))
        self.assertIn(self.events(other_id, run["id"], {"seq": 1, "kind": "started"}).status_code, (403, 404))
        self.assertIn(self.request("GET", package_path).status_code, (401, 403))

    def test_one_host_has_one_lease_and_other_hosts_run_independently(self):
        host_id = self.register()
        other_id = self.register()
        first = self.queue(host_id)
        second = self.queue(host_id)
        independent = self.queue(other_id)
        self.assertEqual(self.heartbeat(host_id)["run"]["id"], first["id"])
        self.assertEqual(self.heartbeat(host_id, active_run_id=first["id"])["host"]["active_run_id"], first["id"])
        self.assertEqual(self.heartbeat(other_id)["run"]["id"], independent["id"])
        self.assertEqual(self.detail(second["id"])["status"], "queued")
        self.success(self.events(host_id, first["id"], {"seq": 1, "kind": "finished", "status": "succeeded", "exit_code": 0}))
        self.assertEqual(self.heartbeat(host_id)["run"]["id"], second["id"])

    def test_active_host_cannot_disable_dispatch_before_stopping(self):
        host_id, run = self.leased()
        self.assertEqual(self.request("PATCH", f"/api/hosts/{host_id}/mode", {"dispatch_enabled": False}).status_code, 409)
        self.assertTrue(self.host(host_id)["dispatch_enabled"])
        self.success(self.request("POST", f"/api/remote-runs/{run['id']}/stop"))
        self.success(self.events(host_id, run["id"], {"seq": 1, "kind": "finished", "status": "cancelled", "exit_code": None}))
        self.assertFalse(self.success(self.request("PATCH", f"/api/hosts/{host_id}/mode", {"dispatch_enabled": False}))["dispatch_enabled"])

    def test_events_retry_is_idempotent_and_altered_replay_or_gap_conflicts(self):
        host_id, run = self.leased()
        started = {"seq": 1, "kind": "started", "text": "开始"}
        output = {"seq": 2, "kind": "stdout", "text": "第二行\n"}
        self.assertEqual(self.success(self.events(host_id, run["id"], started, output))["ack_seq"], 2)
        self.assertEqual(self.success(self.events(host_id, run["id"], started, output))["ack_seq"], 2)
        self.assertEqual(self.events(host_id, run["id"], {**output, "text": "更改旧日志"}).status_code, 409)
        self.assertEqual(self.events(host_id, run["id"], {"seq": 4, "kind": "stdout", "text": "缺少第三条"}).status_code, 409)
        detail = self.detail(run["id"])
        self.assertEqual(detail["ack_seq"], 2)
        self.assertEqual(len(detail["events"]), 2)

    def test_stdout_progress_is_assembled_persisted_and_exposed(self):
        host_id, run = self.leased()
        prefix = 'SPIDERFLY_PROGRESS {"event":"progress","stage":"分页采集",'
        self.success(self.events(
            host_id, run["id"],
            {"seq": 1, "kind": "started"},
            {"seq": 2, "kind": "stdout", "text": prefix},
            {"seq": 3, "kind": "stdout", "text": '"page":2,"collected":18,"total":30}\n普通日志\n'},
        ))
        detail = self.detail(run["id"])
        progress = detail["collection_progress"]
        self.assertEqual(progress["latest"]["stage"], "分页采集")
        self.assertEqual(progress["latest"]["page"], 2)
        self.assertEqual(progress["latest"]["collected"], 18)
        self.assertEqual(progress["latest"]["total"], 30)
        self.assertEqual(progress["event_count"], 1)
        listed = next(item for item in self.success(self.request("GET", "/api/remote-runs")) if item["id"] == run["id"])
        self.assertEqual(listed["collection_progress"]["latest"]["collected"], 18)

    def test_remote_failure_notice_is_immediate_admin_only_and_read_per_user(self):
        host_id, run = self.leased()
        self.success(self.events(
            host_id, run["id"],
            {"seq": 1, "kind": "finished", "status": "failed", "exit_code": 7,
             "text": "synthetic boom token=sk-super-secret-value"},
        ))
        notices = self.success(self.request("GET", "/api/remote-run-notices"))
        self.assertEqual(notices["unread_count"], 1)
        self.assertEqual(notices["items"][0]["id"], run["id"])
        self.assertIn("synthetic boom", notices["items"][0]["error"])
        self.assertNotIn("sk-super-secret-value", notices["items"][0]["error"])
        self.assertIsNone(notices["items"][0]["read_at"])
        self.assertEqual(self.request("GET", "/api/remote-run-notices", user="member").status_code, 403)
        self.assertEqual(self.request(
            "POST", f"/api/remote-run-notices/{run['id']}/read", user="member",
        ).status_code, 403)
        self.success(self.request("POST", f"/api/remote-run-notices/{run['id']}/read"))
        read = self.success(self.request("GET", "/api/remote-run-notices"))
        self.assertEqual(read["unread_count"], 0)
        self.assertIsNotNone(read["items"][0]["read_at"])
        self.success(self.request("POST", f"/api/remote-run-notices/{run['id']}/read"))
        self.assertEqual(database.fetch_one(
            "SELECT COUNT(*) AS count FROM remote_run_notice_reads WHERE run_id=?", (run["id"],),
        )["count"], 1)

    def test_remote_failure_feishu_delivery_is_one_attempt_and_redacted(self):
        database.execute("UPDATE tasks SET notify_on_failure=1 WHERE id=?", (self.task_id,))
        host_id, run = self.leased()
        self.success(self.events(
            host_id, run["id"],
            {"seq": 1, "kind": "finished", "status": "failed", "exit_code": 7,
             "text": "synthetic boom token=sk-super-secret-value"},
        ))
        self.assertEqual(self.detail(run["id"])["notification_status"], "pending")
        with patch("app.feishu.FeishuNotifier") as factory:
            notifier = factory.return_value
            notifier.configured = True
            self.assertTrue(host_dispatch.notify_next())
            self.assertFalse(host_dispatch.notify_next())
        notifier.send_final_result.assert_called_once()
        sent = notifier.send_final_result.call_args.kwargs
        self.assertEqual(sent["status"], "failed")
        self.assertIn(f"远程 #{run['id']}", sent["task_name"])
        self.assertIn("合成 Windows 宿主机", sent["task_name"])
        self.assertIn("synthetic boom", sent["error_summary"])
        self.assertNotIn("sk-super-secret-value", sent["error_summary"])
        self.assertEqual(sent["manual_action_url"], f"http://testserver/?remote_run={run['id']}")
        detail = self.detail(run["id"])
        self.assertEqual(detail["notification_status"], "sent")
        self.assertEqual(detail["notification_error"], "飞书通知已发送")

    def test_remote_failure_notification_respects_switch_and_records_unconfigured(self):
        host_id, disabled = self.leased()
        self.success(self.events(
            host_id, disabled["id"],
            {"seq": 1, "kind": "finished", "status": "failed", "exit_code": 1, "text": "disabled"},
        ))
        self.assertEqual(self.detail(disabled["id"])["notification_status"], "disabled")
        self.assertFalse(host_dispatch.notify_next())

        database.execute("UPDATE tasks SET notify_on_failure=1 WHERE id=?", (self.task_id,))
        queued = self.queue(host_id)
        self.assertEqual(self.heartbeat(host_id)["run"]["id"], queued["id"])
        self.success(self.agent(host_id, "POST", f"/api/agent/runs/{queued['id']}/claim"))
        self.success(self.events(
            host_id, queued["id"],
            {"seq": 1, "kind": "finished", "status": "timed_out", "exit_code": None, "text": "timeout"},
        ))
        with patch("app.feishu.FeishuNotifier") as factory:
            factory.return_value.configured = False
            self.assertTrue(host_dispatch.notify_next())
        factory.return_value.send_final_result.assert_not_called()
        detail = self.detail(queued["id"])
        self.assertEqual(detail["notification_status"], "skipped")
        self.assertIn("未配置飞书", detail["notification_error"])

    def test_remote_failure_notification_failure_is_not_retried(self):
        database.execute("UPDATE tasks SET notify_on_failure=1 WHERE id=?", (self.task_id,))
        host_id, run = self.leased()
        self.success(self.events(
            host_id, run["id"],
            {"seq": 1, "kind": "finished", "status": "failed", "exit_code": 2, "text": "boom"},
        ))
        with patch("app.feishu.FeishuNotifier") as factory:
            notifier = factory.return_value
            notifier.configured = True
            notifier.send_final_result.side_effect = RuntimeError("send failed token=sk-synthetic-secret-value")
            self.assertTrue(host_dispatch.notify_next())
            self.assertFalse(host_dispatch.notify_next())
        notifier.send_final_result.assert_called_once()
        detail = self.detail(run["id"])
        self.assertEqual(detail["notification_status"], "failed")
        self.assertIn("send failed", detail["notification_error"])
        self.assertNotIn("sk-synthetic-secret-value", detail["notification_error"])

    def test_invalid_event_batch_rolls_back_earlier_entries(self):
        host_id, run = self.leased()
        response = self.events(host_id, run["id"],
            {"seq": 1, "kind": "started"}, {"seq": 3, "kind": "stdout", "text": "gap"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.detail(run["id"])["ack_seq"], 0)
        self.assertEqual(self.detail(run["id"])["events"], [])

    def test_terminal_event_cannot_be_overwritten_but_exact_replay_is_accepted(self):
        host_id, run = self.leased()
        finished = {"seq": 1, "kind": "finished", "status": "failed", "exit_code": 1, "text": "original failure"}
        self.success(self.events(host_id, run["id"], finished))
        self.success(self.events(host_id, run["id"], finished))
        for event in ({**finished, "status": "succeeded", "exit_code": 0},
                      {"seq": 2, "kind": "finished", "status": "succeeded", "exit_code": 0},
                      {"seq": 2, "kind": "started"}):
            self.assertEqual(self.events(host_id, run["id"], event).status_code, 409)
        detail = self.detail(run["id"])
        self.assertEqual((detail["status"], detail["exit_code"], detail["ack_seq"]), ("failed", 1, 1))
        self.assertIsNone(self.host(host_id)["active_run_id"])

    def test_event_size_and_batch_limits(self):
        host_id, run = self.leased()
        cases = [
            [{"seq": 1, "kind": "stdout", "text": "x" * 16001}],
            [{"seq": seq, "kind": "stdout", "text": "x"} for seq in range(1, 102)],
        ]
        for batch in cases:
            self.assertIn(self.events(host_id, run["id"], *batch).status_code, (400, 413, 422))
        self.assertEqual(self.detail(run["id"])["ack_seq"], 0)

    def test_stop_queued_run_cancels_without_dispatch(self):
        host_id = self.register()
        run = self.queue(host_id)
        stopped = self.success(self.request("POST", f"/api/remote-runs/{run['id']}/stop"))
        self.assertEqual(stopped["status"], "cancelled")
        self.assertIsNone(self.heartbeat(host_id)["run"])
        self.assertEqual(self.success(self.request("POST", f"/api/remote-runs/{run['id']}/stop"))["status"], "cancelled")

    def test_stop_active_run_keeps_lease_until_agent_terminal_confirmation(self):
        host_id, run = self.leased()
        second = self.queue(host_id)
        stopped = self.success(self.request("POST", f"/api/remote-runs/{run['id']}/stop"))
        self.assertEqual(stopped["status"], "stopping")
        heartbeat = self.heartbeat(host_id, active_run_id=run["id"])
        self.assertTrue(heartbeat["stop_requested"])
        self.assertEqual(self.host(host_id)["active_run_id"], run["id"])
        self.assertEqual(self.detail(second["id"])["status"], "queued")
        self.success(self.events(host_id, run["id"], {"seq": 1, "kind": "finished", "status": "cancelled", "exit_code": None}))
        self.assertEqual(self.heartbeat(host_id)["run"]["id"], second["id"])

    def test_stop_before_package_download_returns_cancel_signal_without_source(self):
        host_id, run = self.leased()
        self.success(self.request("POST", f"/api/remote-runs/{run['id']}/stop"))
        package = self.success(self.agent(host_id, "GET", f"/api/agent/runs/{run['id']}/package"))
        self.assertEqual(package, {"stop_requested": True})
        self.assertEqual(self.host(host_id)["active_run_id"], run["id"])
        self.assertEqual(self.detail(run["id"])["status"], "stopping")
        self.success(self.events(host_id, run["id"], {"seq": 1, "kind": "finished", "status": "cancelled", "exit_code": None}))
        self.assertEqual(self.detail(run["id"])["status"], "cancelled")
        self.assertIsNone(self.host(host_id)["active_run_id"])

    def test_lost_unclaimed_offer_is_safely_requeued_and_offered_again(self):
        host_id = self.register()
        run = self.queue(host_id)
        self.heartbeat(host_id)  # Simulate the response being lost before local claim.
        stale = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        database.execute("UPDATE agent_hosts SET last_seen_at=? WHERE id=?", (stale, host_id))
        self.assertEqual(self.detail(run["id"])["status"], "queued")
        self.assertIsNone(self.host(host_id)["active_run_id"])
        reconnected = self.heartbeat(host_id)
        self.assertEqual(reconnected["run"]["id"], run["id"])
        self.assertEqual(reconnected["host"]["active_run_id"], run["id"])

    def test_durable_local_claim_reattaches_after_offer_timeout_before_authorization(self):
        host_id = self.register()
        run = self.queue(host_id)
        self.heartbeat(host_id)  # Agent persisted the offer, but claim POST was lost.
        stale = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        database.execute("UPDATE agent_hosts SET last_seen_at=? WHERE id=?", (stale, host_id))
        self.detail(run["id"])
        reconnected = self.heartbeat(host_id, active_run_id=run["id"])
        self.assertEqual(reconnected["run"]["id"], run["id"])
        authorized = self.success(self.agent(host_id, "POST", f"/api/agent/runs/{run['id']}/claim"))
        self.assertEqual(authorized["package_hash"], run["package_hash"])
        self.success(self.agent(host_id, "GET", f"/api/agent/runs/{run['id']}/package"))

    def test_package_requires_durable_claim_and_claim_retry_is_idempotent(self):
        host_id = self.register()
        run = self.queue(host_id)
        self.heartbeat(host_id)
        package_path = f"/api/agent/runs/{run['id']}/package"
        self.assertEqual(self.agent(host_id, "GET", package_path).status_code, 409)
        first = self.success(self.agent(host_id, "POST", f"/api/agent/runs/{run['id']}/claim"))
        retry = self.success(self.agent(host_id, "POST", f"/api/agent/runs/{run['id']}/claim"))
        self.assertEqual(first, retry)
        self.success(self.agent(host_id, "GET", package_path))

    def test_controller_never_reoffers_an_authorized_claim_to_empty_journal(self):
        host_id = self.register()
        run = self.queue(host_id)
        self.heartbeat(host_id)
        self.success(self.agent(host_id, "POST", f"/api/agent/runs/{run['id']}/claim"))
        response = self.heartbeat(host_id, active_run_id=None)
        self.assertIsNone(response["run"])
        self.assertEqual(response["host"]["state"], "uncertain")
        self.assertEqual(self.detail(run["id"])["status"], "uncertain")

    def test_late_started_event_does_not_clear_stop_request(self):
        host_id, run = self.leased()
        self.success(self.request("POST", f"/api/remote-runs/{run['id']}/stop"))
        result = self.success(self.events(host_id, run["id"], {"seq": 1, "kind": "started"}))
        self.assertTrue(result["stop_requested"])
        self.assertEqual(self.detail(run["id"])["status"], "stopping")

    def test_disconnect_and_recovery_keep_uncertain_lease_without_running_next_task(self):
        host_id, run = self.leased()
        second = self.queue(host_id)
        self.success(self.events(host_id, run["id"], {"seq": 1, "kind": "started"}))
        stale = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        database.execute("UPDATE agent_hosts SET last_seen_at=? WHERE id=?", (stale, host_id))
        self.assertEqual(self.host(host_id)["state"], "uncertain")
        self.assertEqual(self.detail(run["id"])["status"], "uncertain")
        reconnected = self.heartbeat(host_id, active_run_id=run["id"], recovery_required=True)
        self.assertEqual(reconnected["host"]["active_run_id"], run["id"])
        self.assertEqual(reconnected["host"]["state"], "uncertain")
        self.assertEqual(self.detail(second["id"])["status"], "queued")
        self.assertNotEqual((reconnected["run"] or {}).get("id"), second["id"])
        self.success(self.events(host_id, run["id"], {"seq": 2, "kind": "finished", "status": "failed", "exit_code": 1}))
        self.assertEqual(self.heartbeat(host_id, recovery_required=False)["run"]["id"], second["id"])

    def test_migration_is_repeatable_without_losing_active_lease_or_events(self):
        host_id, run = self.leased()
        self.success(self.events(host_id, run["id"], {"seq": 1, "kind": "started"}))
        host_dispatch.init_tables()
        host_dispatch.init_tables()
        self.assertEqual(self.host(host_id)["active_run_id"], run["id"])
        self.assertEqual(self.detail(run["id"])["ack_seq"], 1)
        self.assertEqual(len(self.detail(run["id"])["events"]), 1)

    def test_artifact_upload_replay_conflict_and_authenticated_download(self):
        host_id, run = self.leased()
        path = f"/api/agent/runs/{run['id']}/artifacts/result.csv"
        content = b"name,value\nsynthetic,1\n"
        self.success(self.agent(host_id, "POST", path, raw=content))
        self.success(self.agent(host_id, "POST", path, raw=content))
        self.assertEqual(self.agent(host_id, "POST", path, raw=b"different").status_code, 409)
        artifacts = self.detail(run["id"])["artifacts"]
        self.assertEqual(len(artifacts), 1)
        artifact = artifacts[0]
        self.assertEqual((artifact["name"], artifact["size"], artifact["sha256"]),
                         ("result.csv", len(content), hashlib.sha256(content).hexdigest()))
        response = self.request("GET", artifact["download_url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, content)
        self.assertEqual(self.request("GET", artifact["download_url"], user=None).status_code, 401)
        self.assertEqual(self.request("GET", artifact["download_url"], user="member").status_code, 403)

    def test_artifact_write_requires_assigned_active_host_and_rejects_unsafe_names(self):
        host_id, run = self.leased()
        other_id = self.register()
        prefix = f"/api/agent/runs/{run['id']}/artifacts/"
        self.assertIn(self.agent(other_id, "POST", prefix + "result.txt", raw=b"data").status_code, (403, 404))
        for filename in ("..%5Coutside.txt", "nested%2Foutside.txt", "C%3Aoutside.txt", "%2E%2E"):
            with self.subTest(filename=filename):
                self.assertIn(self.agent(host_id, "POST", prefix + filename, raw=b"data").status_code, (400, 403, 404, 405, 422))
        self.assertEqual(self.detail(run["id"])["artifacts"], [])
        self.success(self.events(host_id, run["id"], {"seq": 1, "kind": "finished", "status": "succeeded", "exit_code": 0}))
        self.assertIn(self.agent(host_id, "POST", prefix + "late.txt", raw=b"data").status_code, (403, 409))

    def test_unicode_artifact_paths_are_signed_and_downloadable(self):
        host_id, run = self.leased()
        content = "姓名,数值\n合成数据,1\n".encode("utf-8")
        self.success(self.agent(host_id, "POST", f"/api/agent/runs/{run['id']}/artifacts/中文结果.csv", raw=content))
        artifact = self.detail(run["id"])["artifacts"][0]
        self.assertEqual(artifact["name"], "中文结果.csv")
        response = self.request("GET", artifact["download_url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, content)

    def test_unicode_case_colliding_artifact_names_cannot_overwrite_windows_file(self):
        host_id, run = self.leased()
        prefix = f"/api/agent/runs/{run['id']}/artifacts/"
        self.success(self.agent(host_id, "POST", prefix + "Évidence.txt", raw=b"original evidence"))
        self.assertEqual(self.agent(host_id, "POST", prefix + "évidence.txt", raw=b"changed evidence").status_code, 409)
        artifact = self.detail(run["id"])["artifacts"][0]
        response = self.request("GET", artifact["download_url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"original evidence")

    def test_artifact_upload_rejects_oversized_body(self):
        host_id, run = self.leased()
        response = self.agent(host_id, "POST", f"/api/agent/runs/{run['id']}/artifacts/large.bin", raw=b"x" * (16 * 1024 * 1024 + 1))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.detail(run["id"])["artifacts"], [])

    def test_member_cannot_stop_or_read_remote_run_details(self):
        host_id, run = self.leased()
        for method, path in (("GET", f"/api/remote-runs/{run['id']}"),
                             ("POST", f"/api/remote-runs/{run['id']}/stop")):
            self.assertEqual(self.request(method, path, user="member").status_code, 403)
        self.assertEqual(self.detail(run["id"])["status"], "preparing")

    def test_remote_failure_candidate_requires_admin_confirmation_before_rerun(self):
        host_id, run = self.leased()
        original_version = run["code_version_id"]
        self.success(self.events(
            host_id, run["id"],
            {"seq": 1, "kind": "stderr", "text": "ValueError: synthetic failure"},
            {"seq": 2, "kind": "finished", "status": "failed", "exit_code": 1,
             "text": "ValueError: synthetic failure"},
        ))
        pending = self.detail(run["id"])["maintenance"]
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(database.fetch_one("SELECT COUNT(*) AS count FROM remote_runs")["count"], 1)
        self.assertEqual(
            database.fetch_one("SELECT active_version_id FROM maintenance_policies WHERE task_id=?", (self.task_id,))["active_version_id"],
            original_version,
        )

        repaired_source = "print('synthetic repair candidate')\n"
        reply = {
            "usage": {"prompt_tokens": 30, "completion_tokens": 20},
            "choices": [{"message": {"tool_calls": [{"function": {
                "name": "submit_fix",
                "arguments": json.dumps({
                    "source": repaired_source,
                    "explanation": "修正合成异常，保留原任务行为；尚未试跑。",
                }, ensure_ascii=False),
            }}]}}],
        }
        with patch.object(remote_maintenance.ai_settings, "model_request", return_value=reply):
            self.assertTrue(asyncio.run(remote_maintenance.generate_next()))

        candidate = self.detail(run["id"])["maintenance"]
        self.assertEqual(candidate["status"], "candidate")
        self.assertEqual(candidate["sequence"], 2)
        self.assertIsNotNone(candidate["update_id"])
        self.assertEqual(database.fetch_one("SELECT COUNT(*) AS count FROM remote_runs")["count"], 1)
        self.assertEqual(
            database.fetch_one("SELECT active_version_id FROM maintenance_policies WHERE task_id=?", (self.task_id,))["active_version_id"],
            original_version,
        )

        activated = self.success(self.request(
            "POST", f"/api/task-versions/updates/{candidate['update_id']}/activate"
        ))
        self.assertEqual(activated["version"], 2)
        self.assertIsInstance(activated["remote_run_id"], int)
        rerun = self.detail(activated["remote_run_id"])
        self.assertEqual((rerun["host_id"], rerun["task_id"], rerun["version_sequence"]),
                         (host_id, self.task_id, 2))
        self.assertEqual(rerun["controller_url"], "http://testserver")
        self.assertEqual(rerun["status"], "queued")
        self.assertEqual(database.fetch_one(
            "SELECT source FROM task_code_versions WHERE id=?", (rerun["code_version_id"],)
        )["source"], repaired_source)
        completed = self.detail(run["id"])["maintenance"]
        self.assertEqual(completed["status"], "activated")
        self.assertEqual(completed["rerun_remote_run_id"], rerun["id"])

    def test_remote_maintenance_can_return_advice_without_creating_or_running_code(self):
        host_id, run = self.leased()
        self.success(self.events(
            host_id, run["id"],
            {"seq": 1, "kind": "finished", "status": "timed_out", "exit_code": None,
             "text": "外部桌面软件未响应，现有证据不足"},
        ))
        reply = {
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            "choices": [{"message": {"content": "需要管理员先确认目标电脑上的桌面软件状态。"}}],
        }
        with patch.object(remote_maintenance.ai_settings, "model_request", return_value=reply):
            self.assertTrue(asyncio.run(remote_maintenance.generate_next()))
        result = self.detail(run["id"])["maintenance"]
        self.assertEqual(result["status"], "review")
        self.assertIn("桌面软件", result["note"])
        self.assertIsNone(result["candidate_version_id"])
        self.assertEqual(database.fetch_one("SELECT COUNT(*) AS count FROM remote_runs")["count"], 1)
        self.assertEqual(database.fetch_one(
            "SELECT COUNT(*) AS count FROM task_code_versions WHERE task_id=?", (self.task_id,)
        )["count"], 1)

    def test_remote_candidate_activation_rolls_back_when_original_host_is_revoked(self):
        host_id, run = self.leased()
        self.success(self.events(
            host_id, run["id"],
            {"seq": 1, "kind": "finished", "status": "failed", "exit_code": 1, "text": "boom"},
        ))
        reply = {
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            "choices": [{"message": {"tool_calls": [{"function": {
                "name": "submit_fix",
                "arguments": json.dumps({
                    "source": "print('candidate for revoked host')\n",
                    "explanation": "修正合成失败；尚未试跑。",
                }, ensure_ascii=False),
            }}]}}],
        }
        with patch.object(remote_maintenance.ai_settings, "model_request", return_value=reply):
            asyncio.run(remote_maintenance.generate_next())
        candidate = self.detail(run["id"])["maintenance"]
        original_version = run["code_version_id"]
        self.success(self.request("POST", f"/api/hosts/{host_id}/revoke"))
        response = self.request("POST", f"/api/task-versions/updates/{candidate['update_id']}/activate")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(database.fetch_one(
            "SELECT active_version_id FROM maintenance_policies WHERE task_id=?", (self.task_id,)
        )["active_version_id"], original_version)
        self.assertEqual(database.fetch_one(
            "SELECT status FROM task_version_updates WHERE id=?", (candidate["update_id"],)
        )["status"], "candidate")
        self.assertEqual(database.fetch_one(
            "SELECT status FROM remote_maintenance_jobs WHERE remote_run_id=?", (run["id"],)
        )["status"], "candidate")
        self.assertEqual(database.fetch_one("SELECT COUNT(*) AS count FROM remote_runs")["count"], 1)

    def test_scheduled_dispatch_freezes_one_remote_occurrence_and_is_idempotent(self):
        host_id = self.register(enabled=False)
        scheduled_for = "2026-09-21T01:00:00+00:00"
        database.execute(
            """UPDATE tasks SET target_host_id=?,trigger_type='daily',
               trigger_config='{"time":"09:00"}',last_triggered_at=? WHERE id=?""",
            (host_id, scheduled_for, self.task_id),
        )
        with patch.object(host_dispatch, "SERVER_URL", "http://192.168.1.50:9356"):
            first = host_dispatch.dispatch_scheduled_task(self.task_id)
            second = host_dispatch.dispatch_scheduled_task(self.task_id)
        self.assertEqual(first, second)
        self.assertEqual(database.fetch_one("SELECT COUNT(*) AS count FROM executions")["count"], 0)
        run = database.fetch_one("SELECT * FROM remote_runs WHERE id=?", (first,))
        self.assertEqual(run["host_id"], host_id)
        self.assertEqual(run["trigger_source"], "schedule")
        self.assertEqual(run["scheduled_for"], scheduled_for)
        self.assertEqual(run["controller_url"], "http://192.168.1.50:9356")
        self.assertEqual(run["status"], "queued")
        self.assertIsNotNone(run["code_version_id"])

    def test_revoking_target_host_disables_future_schedule_and_cancels_queued_run(self):
        host_id = self.register()
        database.execute(
            """UPDATE tasks SET target_host_id=?,trigger_type='daily',
               trigger_config='{"time":"09:00"}',next_run_at='2026-09-22T01:00:00+00:00'
               WHERE id=?""",
            (host_id, self.task_id),
        )
        run_id = host_dispatch.dispatch_scheduled_task(self.task_id)
        self.success(self.request("POST", f"/api/hosts/{host_id}/revoke"))
        task = database.fetch_one("SELECT enabled,next_run_at,target_host_id FROM tasks WHERE id=?", (self.task_id,))
        self.assertEqual(task, {"enabled": 0, "next_run_at": None, "target_host_id": host_id})
        run = database.fetch_one("SELECT status,error FROM remote_runs WHERE id=?", (run_id,))
        self.assertEqual(run["status"], "cancelled")
        self.assertIn("撤销", run["error"])


if __name__ == "__main__":
    unittest.main()
