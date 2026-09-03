from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch

from app import database, execution_artifacts, main, security


class ExecutionArtifactApiTests(unittest.TestCase):
    """Exercise real routes and cookie dependencies without starting workers."""

    def setUp(self) -> None:
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        data = self.root / "data"
        self.executions = data / "executions"
        for module, name, value in (
            (database, "DATA_DIR", data),
            (database, "DB_PATH", data / "spiderfly.db"),
            (database, "RPA_APPS_DIR", data / "apps"),
            (database, "RPA_ENVS_DIR", data / "envs"),
            (execution_artifacts, "EXECUTIONS_DIR", self.executions),
        ):
            stack.enter_context(patch.object(module, name, value))
        database.init_db()
        now = database.utc_now()
        self.user_id = database.execute(
            """INSERT INTO users (username, display_name, password_hash, role,
            active, must_change_password, created_at, updated_at)
            VALUES ('file-operator', '文件操作员', 'unused', 'operator', 1, 0, ?, ?)""",
            (now, now),
        )
        self.token, _ = security.create_session(self.user_id)
        self.task_id = database.execute(
            """INSERT INTO tasks (name, script_path, created_at, updated_at)
            VALUES ('文件交付示例', 'unused.py', ?, ?)""",
            (now, now),
        )
        self.execution_id = self.add_execution("success")
        self.file = self.executions / str(self.execution_id) / "artifacts" / "姓名合并示例" / "结果.xlsx"
        self.file.parent.mkdir(parents=True)
        self.content = b"PK\x03\x04" + bytes(range(256)) * 600
        self.file.write_bytes(self.content)
        self.relative = "姓名合并示例/结果.xlsx"

    def add_execution(self, status: str) -> int:
        return database.execute(
            "INSERT INTO executions (task_id, status, created_at) VALUES (?, ?, ?)",
            (self.task_id, status, database.utc_now()),
        )

    def request(self, *, path: str | None = None, method: str = "GET",
                download: bool = True, logged_in: bool = True,
                execution_id: int | None = None) -> tuple[int, dict[str, str], bytes]:
        route = f"/api/executions/{execution_id or self.execution_id}"
        query = b""
        if download:
            route += "/artifacts/download"
            query = urlencode({"path": path if path is not None else self.relative}).encode()
        headers = []
        if logged_in:
            headers.append((b"cookie", f"{security.SESSION_COOKIE_NAME}={self.token}".encode()))
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": method, "scheme": "http",
            "path": route, "raw_path": route.encode(), "query_string": query,
            "root_path": "", "headers": headers,
            "server": ("testserver", 80), "client": ("127.0.0.1", 12345),
        }
        messages = []

        async def send(message):
            messages.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        asyncio.run(main.app(scope, receive, send))
        start = next(message for message in messages if message["type"] == "http.response.start")
        response_headers = {key.decode(): value.decode() for key, value in start["headers"]}
        body = b"".join(message.get("body", b"") for message in messages
                        if message["type"] == "http.response.body")
        return start["status"], response_headers, body

    def test_operator_can_list_and_download_exact_bytes(self):
        status, _, body = self.request(download=False)
        self.assertEqual(status, 200)
        detail = json.loads(body)
        self.assertEqual(detail["artifacts"], {
            "files": [{"path": self.relative, "name": "结果.xlsx", "size_bytes": len(self.content)}],
            "truncated": False, "error": "",
        })
        self.assertNotIn(str(self.root), body.decode())
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(body, self.content)
        self.assertEqual(headers["content-type"], "application/octet-stream")
        self.assertEqual(int(headers["content-length"]), len(self.content))
        self.assertIn("attachment;", headers["content-disposition"])
        self.assertIn("filename*=utf-8''", headers["content-disposition"].lower())
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertIn("no-store", headers["cache-control"])

    def test_head_matches_get_headers_and_closes_file_without_body(self):
        opened = []
        original = main.open_artifact

        def capture(*args):
            stream = original(*args)
            opened.append(stream)
            return stream

        with patch.object(main, "open_artifact", side_effect=capture):
            status, head_headers, body = self.request(method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertTrue(opened[0].closed)
        self.assertEqual(head_headers, self.request()[1])

    def test_unauthenticated_and_password_change_users_cannot_read_files(self):
        with patch.object(main, "list_artifacts") as listing, patch.object(main, "open_artifact") as opening:
            for method in ("GET", "HEAD"):
                self.assertEqual(self.request(method=method, logged_in=False)[0], 401)
            self.assertEqual(self.request(download=False, logged_in=False)[0], 401)
            database.execute("UPDATE users SET must_change_password = 1 WHERE id = ?", (self.user_id,))
            for method in ("GET", "HEAD"):
                self.assertEqual(self.request(method=method)[0], 403)
            self.assertEqual(self.request(download=False)[0], 403)
            listing.assert_not_called()
            opening.assert_not_called()

    def test_expired_or_disabled_session_is_rejected(self):
        database.execute("UPDATE users SET active = 0 WHERE id = ?", (self.user_id,))
        self.assertEqual(self.request()[0], 401)
        database.execute("UPDATE users SET active = 1 WHERE id = ?", (self.user_id,))
        database.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.request()[0], 401)

    def test_nonterminal_states_never_list_or_open_files(self):
        with patch.object(main, "list_artifacts") as listing, patch.object(main, "open_artifact") as opening:
            for state in ("pending", "running", "unexpected"):
                with self.subTest(state=state):
                    database.execute("UPDATE executions SET status = ? WHERE id = ?", (state, self.execution_id))
                    status, _, body = self.request(download=False)
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(body)["artifacts"]["files"], [])
                    self.assertEqual(self.request()[0], 409)
                    self.assertEqual(self.request(method="HEAD")[0], 409)
            listing.assert_not_called()
            opening.assert_not_called()

    def test_all_terminal_states_offer_saved_files(self):
        for state in ("success", "failed", "timeout", "cancelled"):
            with self.subTest(state=state):
                database.execute("UPDATE executions SET status = ? WHERE id = ?", (state, self.execution_id))
                self.assertEqual(self.request()[2], self.content)
                self.assertEqual(len(json.loads(self.request(download=False)[2])["artifacts"]["files"]), 1)

    def test_missing_or_deleted_record_checked_before_files(self):
        with patch.object(main, "list_artifacts") as listing, patch.object(main, "open_artifact") as opening:
            self.assertEqual(self.request(execution_id=99999)[0], 404)
            self.assertEqual(self.request(execution_id=99999, download=False)[0], 404)
            database.execute("DELETE FROM tasks WHERE id = ?", (self.task_id,))
            self.assertEqual(self.request()[0], 404)
            listing.assert_not_called()
            opening.assert_not_called()

    def test_history_without_artifacts_is_empty_and_disappeared_file_is_404(self):
        old_id = self.add_execution("success")
        self.assertEqual(json.loads(self.request(execution_id=old_id, download=False)[2])["artifacts"],
                         {"files": [], "truncated": False, "error": ""})
        self.file.unlink()
        self.assertEqual(self.request()[0], 404)
        self.assertEqual(self.request(method="HEAD")[0], 404)

    def test_invalid_and_private_paths_are_not_downloadable(self):
        for path in ("../result.json", "/etc/passwd", "C:/Windows/win.ini", "..\\secret",
                     "姓名合并示例/结果.xlsx:secret", ".hidden", "~$book.xlsx", "book.tmp"):
            with self.subTest(path=path):
                status, _, body = self.request(path=path)
                self.assertEqual(status, 404)
                self.assertEqual(json.loads(body)["detail"], "文件不存在或不可下载")

    def test_read_error_is_generic_and_failed_response_construction_closes_file(self):
        with patch.object(main, "open_artifact", side_effect=PermissionError(str(self.file))):
            status, _, body = self.request()
            self.assertEqual(status, 404)
            self.assertNotIn(str(self.root), body.decode())
        with self.file.open("rb") as stream:
            with patch.object(main, "open_artifact", return_value=stream), \
                    patch.object(main, "ArtifactDownloadResponse", side_effect=OSError("gone")):
                self.assertEqual(self.request()[0], 404)
                self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
