from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from app import database, main, security


class MemberManagementTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for key, value in {"DATA_DIR": root, "DB_PATH": root / "test.db",
                           "RPA_APPS_DIR": root / "apps", "RPA_ENVS_DIR": root / "envs"}.items():
            self.stack.enter_context(patch.object(database, key, value))
        self.stack.enter_context(patch.object(security, "PASSWORD_ITERATIONS", 1000))
        database.init_db()
        self.admin = security.create_user("boss", "管理员", "admin", "AdminPassword123")
        database.execute("UPDATE users SET role = 'super_admin' WHERE id = ?", (self.admin["id"],))
        self.admin = security.public_user(database.fetch_one("SELECT * FROM users WHERE id = ?", (self.admin["id"],)))
        self.member = security.create_user("member", "普通成员", "operator", "MemberPassword123")
        self.admin_token = security.create_session(self.admin["id"])[0]
        self.member_token = security.create_session(self.member["id"])[0]

    def request(self, method, url, body=None, token=None):
        async def run():
            parsed = urlsplit(url)
            raw = json.dumps(body).encode() if body is not None else b""
            headers = [(b"host", b"testserver"), (b"content-type", b"application/json")]
            if token:
                headers.append((b"cookie", (security.SESSION_COOKIE_NAME + "=" + token).encode()))
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": method, "scheme": "http", "path": parsed.path,
                     "raw_path": parsed.path.encode(), "query_string": parsed.query.encode(),
                     "headers": headers, "client": ("127.0.0.1", 12345), "server": ("testserver", 80)}
            messages = []

            async def receive():
                return {"type": "http.request", "body": raw, "more_body": False}

            async def send(message):
                messages.append(message)

            await main.app(scope, receive, send)
            status = next(item["status"] for item in messages if item["type"] == "http.response.start")
            content = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
            return status, json.loads(content) if content else None
        return asyncio.run(run())

    def edit(self, changes, target=None):
        target = target or self.member
        return self.request("PATCH", f"/api/users/{target['id']}",
                            {"version": target["version"], **changes}, self.admin_token)

    def test_member_cannot_list_create_edit_or_delete_accounts(self):
        paths = [("GET", "/api/users", None),
                 ("POST", "/api/users", {"username": "new", "display_name": "新增", "role": "admin", "password": "TestPassword123"}),
                 ("PATCH", f"/api/users/{self.member['id']}", {"version": 1, "role": "admin"}),
                 ("DELETE", f"/api/users/{self.admin['id']}?version=1", None)]
        for method, path, body in paths:
            with self.subTest(method=method):
                self.assertEqual(self.request(method, path, body, self.member_token)[0], 403)

    def test_edit_account_name_and_role_invalidates_old_session(self):
        code, result = self.edit({"username": "Renamed", "display_name": "新名称", "role": "admin"})
        self.assertEqual(code, 200)
        self.assertEqual((result["username"], result["role"], result["version"]), ("renamed", "admin", 2))
        self.assertEqual(self.request("GET", "/api/auth/me", token=self.member_token)[0], 401)
        self.assertEqual(self.request("POST", "/api/auth/login", {"username": "renamed", "password": "MemberPassword123"})[0], 200)
        self.assertEqual(self.request("POST", "/api/auth/login", {"username": "member", "password": "MemberPassword123"})[0], 401)

    def test_reset_six_digit_password_is_ready_and_never_leaks_password(self):
        password = "654321"
        code, result = self.edit({"password": password})
        self.assertEqual(code, 200)
        self.assertFalse(result["must_change_password"])
        self.assertNotIn("password_hash", result)
        self.assertNotIn("password", result)
        self.assertEqual(self.request("GET", "/api/auth/me", token=self.member_token)[0], 401)
        self.assertEqual(self.request("POST", "/api/auth/login", {"username": "member", "password": "MemberPassword123"})[0], 401)
        self.assertEqual(self.request("POST", "/api/auth/login", {"username": "member", "password": password})[0], 200)
        new_token = security.create_session(self.member["id"])[0]
        self.assertEqual(self.request("GET", "/api/tasks", token=new_token)[0], 200)
        audit = database.fetch_all("SELECT summary FROM audit_logs WHERE action='update_user'")
        self.assertIn("重置密码", audit[0]["summary"])
        self.assertNotIn(password, json.dumps(audit))

    def test_super_admin_can_demote_admin_and_revoke_privileges(self):
        database.execute("UPDATE users SET role='admin' WHERE id=?", (self.member["id"],))
        self.assertEqual(self.edit({"role": "operator"})[0], 200)
        self.assertEqual(self.request("GET", "/api/users", token=self.member_token)[0], 401)
        token = security.create_session(self.member["id"])[0]
        self.assertEqual(self.request("GET", "/api/users", token=token)[0], 403)

    def test_delete_disables_login_keeps_history_and_reserves_name(self):
        database.execute("INSERT INTO audit_logs (user_id,username,action,created_at) VALUES (?, 'member', 'history', ?)", (self.member["id"], database.utc_now()))
        path = f"/api/users/{self.member['id']}?version=1"
        self.assertEqual(self.request("DELETE", path, token=self.admin_token)[0], 204)
        self.assertEqual(self.request("GET", "/api/auth/me", token=self.member_token)[0], 401)
        self.assertEqual(self.request("POST", "/api/auth/login", {"username": "member", "password": "MemberPassword123"})[0], 401)
        self.assertEqual(len(security.list_users()), 1)
        self.assertIsNotNone(database.fetch_one("SELECT * FROM audit_logs WHERE action='history'"))
        self.assertIsNotNone(database.fetch_one("SELECT deleted_at FROM users WHERE id=?", (self.member["id"],))["deleted_at"])
        self.assertEqual(self.request("DELETE", path, token=self.admin_token)[0], 404)
        self.assertEqual(self.request("POST", "/api/users", {"username": "MEMBER", "display_name": "复用", "role": "operator", "password": "SomePassword123"}, self.admin_token)[0], 409)

    def test_current_admin_cannot_delete_disable_demote_or_reset_self(self):
        for changes in ({"role": "operator"}, {"active": False}, {"password": "NewPassword123"}):
            self.assertEqual(self.edit(changes, self.admin)[0], 400)
        self.assertEqual(self.request("DELETE", f"/api/users/{self.admin['id']}?version=1", token=self.admin_token)[0], 400)
        self.assertIsNotNone(security._session_user(self.admin_token))

    def test_validation_duplicate_and_stale_updates(self):
        for changes, expected in [({"role": "super_admin"}, 422), ({"password": "short"}, 422),
                                  ({"username": None}, 422), ({"display_name": " "}, 400),
                                  ({"username": "BOSS"}, 409), ({"password_hash": "bad"}, 422)]:
            with self.subTest(changes=changes):
                self.assertEqual(self.edit(changes)[0], expected)
        self.assertEqual(self.edit({"display_name": "更新一次"})[0], 200)
        self.assertEqual(self.edit({"display_name": "旧表单覆盖"})[0], 409)
        self.assertEqual(self.request("DELETE", f"/api/users/{self.member['id']}?version=1", token=self.admin_token)[0], 409)

    def test_disable_and_enable_account(self):
        code, updated = self.edit({"active": False})
        self.assertEqual(code, 200)
        self.assertFalse(updated["active"])
        self.assertEqual(self.request("GET", "/api/auth/me", token=self.member_token)[0], 401)
        self.assertEqual(self.edit({"active": True}, updated)[0], 200)
        self.assertIsNotNone(security.authenticate_user("member", "MemberPassword123"))

    def test_repeated_database_migration_preserves_roles_and_passwords(self):
        before = database.fetch_all("SELECT id,role,password_hash,version FROM users")
        database.init_db()
        database.init_db()
        self.assertEqual(before, database.fetch_all("SELECT id,role,password_hash,version FROM users"))

    def test_default_password_can_login_without_initial_change(self):
        code, user = self.request("POST", "/api/users", {"username": "fresh", "display_name": "新成员"}, self.admin_token)
        self.assertEqual(code, 201)
        self.assertFalse(user["must_change_password"])
        code, login = self.request("POST", "/api/auth/login", {"username": "fresh", "password": "123321"})
        self.assertEqual(code, 200)
        self.assertFalse(login["must_change_password"])
        token = security.create_session(user["id"])[0]
        self.assertEqual(self.request("GET", "/api/tasks", token=token)[0], 200)
        self.assertEqual(self.request("GET", "/api/users", token=token)[0], 403)

    def test_six_digit_self_change_and_five_digit_rejection(self):
        for path, body in [("/api/users", {"username": "short", "display_name": "短密码", "password": "12345"}),
                           ("/api/auth/change-password", {"current_password": "AdminPassword123", "new_password": "12345"})]:
            self.assertEqual(self.request("POST", path, body, self.admin_token)[0], 422)
        code, _ = self.request("POST", "/api/auth/change-password", {"current_password": "MemberPassword123", "new_password": "123321"}, self.member_token)
        self.assertEqual(code, 200)
        self.assertIsNotNone(security.authenticate_user("member", "123321"))

    def test_legacy_first_change_flag_is_removed_without_changing_password(self):
        before = database.fetch_all("SELECT id,role,password_hash FROM users")
        database.execute("UPDATE users SET must_change_password = 1")
        database.init_db()
        self.assertEqual(before, database.fetch_all("SELECT id,role,password_hash FROM users"))
        self.assertTrue(all(not row["must_change_password"] for row in database.fetch_all("SELECT * FROM users")))
        self.assertEqual(self.request("GET", "/api/tasks", token=self.member_token)[0], 200)
        self.assertEqual(self.request("GET", "/api/users", token=self.admin_token)[0], 200)
        after = database.fetch_all("SELECT id,version FROM users")
        database.init_db()
        self.assertEqual(after, database.fetch_all("SELECT id,version FROM users"))

    def test_bootstrap_uses_default_password_without_forcing_change(self):
        database.execute("DELETE FROM sessions")
        database.execute("DELETE FROM users")
        with patch.object(security, "DATA_DIR", database.DATA_DIR), patch.object(security, "BOOTSTRAP_FILE", database.DATA_DIR / "login.txt"):
            security.ensure_bootstrap_admin()
            user = security.authenticate_user("admin", "admin")
            self.assertIsNotNone(user)
            self.assertFalse(user["must_change_password"])
            self.assertEqual(user["role"], "super_admin")
            self.assertIn("初始密码：admin", security.BOOTSTRAP_FILE.read_text(encoding="utf-8"))
            code, logged_in = self.request("POST", "/api/auth/login", {"username": "admin", "password": "admin"})
            self.assertEqual(code, 200)
            self.assertEqual(logged_in["role"], "super_admin")
            token = security.create_session(user["id"])[0]
            self.assertEqual(self.request("POST", "/api/auth/change-password", {"current_password": "admin", "new_password": "654321"}, token)[0], 200)
            self.assertEqual(self.request("POST", "/api/auth/login", {"username": "admin", "password": "admin"})[0], 401)
            self.assertIsNotNone(security.authenticate_user("admin", "654321"))

    def test_stale_actor_cannot_manage_members(self):
        database.execute("UPDATE users SET role='operator' WHERE id=?", (self.admin["id"],))
        with self.assertRaises(Exception) as caught:
            security.update_user(self.admin, self.member["id"], {"role": "admin"}, 1)
        self.assertEqual(caught.exception.status_code, 403)

    def test_admin_cannot_manage_any_account_or_change_own_password(self):
        manager = security.create_user("manager", "管理员", "admin", "123321")
        token = security.create_session(manager["id"])[0]
        attempts = [("POST", "/api/users", {"username": "injected", "display_name": "越权创建", "role": "admin"}),
                    ("PATCH", f"/api/users/{self.admin['id']}", {"version": 1, "password": "654321"}),
                    ("DELETE", f"/api/users/{self.admin['id']}?version=1", None),
                    ("PATCH", f"/api/users/{manager['id']}", {"version": 1, "password": "654321"}),
                    ("POST", "/api/auth/change-password", {"current_password": "123321", "new_password": "654321"})]
        for method, path, body in attempts:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path, body, token)[0], 403)
        self.assertIsNotNone(security.authenticate_user("manager", "123321"))
        code, members = self.request("GET", "/api/users", token=token)
        self.assertEqual(code, 200)
        self.assertTrue(all("password_hash" not in item and "password" not in item for item in members))
        self.assertEqual(self.request("GET", "/api/audit-logs", token=token)[0], 200)
        self.assertEqual(self.request("GET", "/api/tasks", token=token)[0], 200)
        self.assertEqual(security.admin_user(manager)["id"], manager["id"])
        self.assertEqual(security.admin_user(self.admin)["id"], self.admin["id"])
        self.assertEqual(self.edit({"password": "654321"}, manager)[0], 200)
        self.assertEqual(self.request("GET", "/api/auth/me", token=token)[0], 401)
        code, user = self.request("POST", "/api/auth/login", {"username": "manager", "password": "654321"})
        self.assertEqual(code, 200)
        self.assertFalse(user["must_change_password"])

    def test_super_admin_can_change_own_password_and_cannot_create_another_owner(self):
        code, _ = self.request("POST", "/api/auth/change-password", {"current_password": "AdminPassword123", "new_password": "123321"}, self.admin_token)
        self.assertEqual(code, 200)
        self.assertIsNotNone(security.authenticate_user("boss", "123321"))
        self.assertEqual(self.request("POST", "/api/users", {"username": "owner2", "display_name": "第二个", "role": "super_admin"}, self.admin_token)[0], 422)
        self.assertEqual(self.edit({"role": "super_admin"})[0], 422)

    def test_migration_promotes_only_original_owner_and_survives_rename(self):
        database.execute("UPDATE users SET role = 'admin', username = 'admin' WHERE id = ?", (self.admin["id"],))
        manager = security.create_user("manager", "管理员", "admin", "123321")
        before = database.fetch_all("SELECT id,password_hash FROM users ORDER BY id")
        database.init_db()
        owners = database.fetch_all("SELECT id FROM users WHERE role = 'super_admin'")
        self.assertEqual(owners, [{"id": self.admin["id"]}])
        self.assertEqual(before, database.fetch_all("SELECT id,password_hash FROM users ORDER BY id"))
        self.assertEqual(database.fetch_one("SELECT role FROM users WHERE id = ?", (manager["id"],))["role"], "admin")
        self.assertIsNone(security._session_user(self.admin_token))
        owner = security.public_user(database.fetch_one("SELECT * FROM users WHERE id = ?", (self.admin["id"],)))
        security.update_user(owner, owner["id"], {"username": "renamed-owner"}, owner["version"])
        database.init_db()
        self.assertEqual(database.fetch_all("SELECT id FROM users WHERE role = 'super_admin'"), owners)
        with self.assertRaises(Exception):
            database.execute("UPDATE users SET role = 'super_admin' WHERE id = ?", (manager["id"],))

    def test_inflight_self_change_rechecks_latest_admin_role(self):
        stale_member = database.fetch_one("SELECT * FROM users WHERE id = ?", (self.member["id"],))
        self.assertEqual(self.edit({"role": "admin"})[0], 200)
        with self.assertRaises(Exception) as caught:
            security.change_password(stale_member, "MemberPassword123", "123321")
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIsNotNone(security.authenticate_user("member", "MemberPassword123"))

    def test_inflight_old_password_change_cannot_undo_admin_reset(self):
        stale = database.fetch_one("SELECT * FROM users WHERE id=?", (self.member["id"],))
        self.assertEqual(self.edit({"password": "ResetByAdmin123"})[0], 200)
        with self.assertRaises(ValueError):
            security.change_password(stale, "MemberPassword123", "OverridePassword123")
        self.assertIsNotNone(security.authenticate_user("member", "ResetByAdmin123"))


if __name__ == "__main__":
    unittest.main()
