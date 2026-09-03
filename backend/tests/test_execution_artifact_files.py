from __future__ import annotations

import asyncio
import ctypes
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

from starlette.requests import ClientDisconnect

from app import execution_artifacts as artifacts


class ArtifactFileTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.executions = self.directory / "executions"
        self.root = self.executions / "17" / "artifacts"
        self.root.mkdir(parents=True)
        setting = patch.object(artifacts, "EXECUTIONS_DIR", self.executions)
        setting.start()
        self.addCleanup(setting.stop)

    def write(self, relative: str, data: bytes = b"test") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_lists_and_opens_only_this_execution_artifacts(self) -> None:
        self.write("姓名合并示例/结果.xlsx", b"exact bytes")
        self.write("empty.txt", b"")
        other = self.executions / "18" / "artifacts" / "private.txt"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"other execution")
        (self.root.parent / "result.json").write_bytes(b"receipt")
        result = artifacts.list_artifacts(17)
        self.assertEqual(
            result,
            {
                "files": [
                    {"path": "empty.txt", "name": "empty.txt", "size_bytes": 0},
                    {
                        "path": "姓名合并示例/结果.xlsx",
                        "name": "结果.xlsx",
                        "size_bytes": 11,
                    },
                ],
                "truncated": False,
                "error": "",
            },
        )
        with artifacts.open_artifact(17, "姓名合并示例/结果.xlsx") as stream:
            self.assertEqual(stream.read(), b"exact bytes")

    def test_hidden_and_temporary_files_and_parents_are_not_downloadable(self) -> None:
        excluded = (
            ".secret", "~$结果.xlsx", "draft.TMP", "download.part", "download.partial",
            ".hidden/visible.txt", "~$folder/visible.txt", "folder.tmp/visible.txt",
        )
        for name in excluded:
            self.write(name)
        self.write("ordinary.xlsx")
        self.assertEqual(
            [item["path"] for item in artifacts.list_artifacts(17)["files"]],
            ["ordinary.xlsx"],
        )
        for name in excluded:
            with self.subTest(name=name), self.assertRaises(ValueError):
                artifacts.open_artifact(17, name)

    def test_rejects_noncanonical_and_windows_special_paths(self) -> None:
        values = (
            "", ".", "..", "../result.json", "folder/../../18/artifacts/secret",
            "/absolute", "//server/share", "C:/file", "C:file", r"C:\file",
            r"folder\file", "result.xlsx:private", "folder//file", "folder/./file",
            "folder/file/", "NUL", "NUL.xlsx", "CON.txt", "COM1", "LPT9.txt",
            "COM¹.txt", "CONIN$", "last.", "last ", "a\x00b", "a\nb", "a?b",
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                artifacts.open_artifact(17, value)
        for execution_id in (0, -1, True, "17"):
            with self.subTest(execution_id=execution_id), self.assertRaises(ValueError):
                artifacts.open_artifact(execution_id, "ordinary.xlsx")

    def test_missing_history_is_empty_and_read_errors_are_generic(self) -> None:
        self.assertEqual(
            artifacts.list_artifacts(99), {"files": [], "truncated": False, "error": ""}
        )
        with self.assertRaises(FileNotFoundError):
            artifacts.open_artifact(17, "absent.xlsx")
        with patch.object(artifacts.os, "scandir", side_effect=PermissionError("C:/secret")):
            result = artifacts.list_artifacts(17)
        self.assertEqual(result["error"], artifacts.UNAVAILABLE_MESSAGE)
        self.assertNotIn("secret", str(result))
        self.root.rmdir()
        self.root.write_bytes(b"not a directory")
        self.assertEqual(artifacts.list_artifacts(17)["error"], artifacts.UNAVAILABLE_MESSAGE)

    def test_file_and_entry_limits_bound_scanning(self) -> None:
        for index in range(3):
            self.write(f"file{index}.txt")
        with patch.object(artifacts, "MAX_FILES", 2):
            result = artifacts.list_artifacts(17)
        self.assertEqual(len(result["files"]), 2)
        self.assertTrue(result["truncated"])
        with patch.object(artifacts, "MAX_ENTRIES", 2):
            result = artifacts.list_artifacts(17)
        self.assertEqual(len(result["files"]), 2)
        self.assertTrue(result["truncated"])
        for path in self.root.iterdir():
            path.unlink()
        for index in range(3):
            (self.root / f"empty{index}").mkdir()
        with patch.object(artifacts, "MAX_ENTRIES", 2):
            result = artifacts.list_artifacts(17)
        self.assertEqual(result["files"], [])
        self.assertTrue(result["truncated"])

    def test_depth_limit_is_shared_by_listing_and_direct_download(self) -> None:
        allowed = "/".join(["folder"] * (artifacts.MAX_DEPTH - 1) + ["allowed.txt"])
        too_deep = "/".join(["folder"] * artifacts.MAX_DEPTH + ["deep.txt"])
        self.write(allowed)
        self.write(too_deep)
        result = artifacts.list_artifacts(17)
        self.assertEqual([item["path"] for item in result["files"]], [allowed])
        self.assertTrue(result["truncated"])
        with artifacts.open_artifact(17, allowed) as stream:
            self.assertEqual(stream.read(), b"test")
        with self.assertRaises(ValueError):
            artifacts.open_artifact(17, too_deep)

    def test_hard_links_to_external_files_are_excluded(self) -> None:
        original = self.directory / "original.txt"
        original.write_bytes(b"private")
        alias = self.root / "alias.txt"
        os.link(original, alias)
        self.assertEqual(artifacts.list_artifacts(17)["files"], [])
        with self.assertRaises(ValueError):
            artifacts.open_artifact(17, "alias.txt")
        self.assertEqual(original.read_bytes(), b"private")

    def test_path_length_limit_matches_listing_and_download(self) -> None:
        allowed = "folder1/" + "a" * 12
        too_long = "folder1/" + "a" * 13
        self.write(allowed)
        self.write(too_long)
        with patch.object(artifacts, "MAX_PATH_CHARS", len(allowed)):
            result = artifacts.list_artifacts(17)
            self.assertEqual([item["path"] for item in result["files"]], [allowed])
            self.assertTrue(result["truncated"])
            with artifacts.open_artifact(17, allowed) as stream:
                self.assertEqual(stream.read(), b"test")
            with self.assertRaises(ValueError):
                artifacts.open_artifact(17, too_long)

    def test_file_and_directory_symlinks_are_excluded(self) -> None:
        outside = self.directory / "outside"
        outside.mkdir()
        original = outside / "secret.txt"
        original.write_bytes(b"private")
        try:
            (self.root / "file-link.txt").symlink_to(original)
            (self.root / "directory-link").symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"系统未授予创建符号链接权限：{exc.winerror if os.name == 'nt' else exc.errno}")
        self.assertEqual(artifacts.list_artifacts(17)["files"], [])
        for relative in ("file-link.txt", "directory-link/secret.txt"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                artifacts.open_artifact(17, relative)

    @unittest.skipUnless(os.name == "nt", "Windows 目录联接")
    def test_actual_windows_junctions_at_each_execution_boundary_are_rejected(self) -> None:
        target = self.directory / "external"
        target.mkdir()
        (target / "secret.txt").write_bytes(b"private")
        (target / "artifacts").mkdir()
        (target / "artifacts" / "secret.txt").write_bytes(b"private")
        cases = (
            (17, self.root / "linked", "linked/secret.txt"),
            (18, self.executions / "18" / "artifacts", "secret.txt"),
            (19, self.executions / "19", "secret.txt"),
        )
        for execution_id, link, relative in cases:
            with self.subTest(location=link):
                link.parent.mkdir(parents=True, exist_ok=True)
                completed = subprocess.run(
                    ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertTrue(link.is_junction())
                try:
                    self.assertEqual(artifacts.list_artifacts(execution_id)["files"], [])
                    with self.assertRaises(ValueError):
                        artifacts.open_artifact(execution_id, relative)
                finally:
                    link.rmdir()
        self.assertEqual((target / "secret.txt").read_bytes(), b"private")

    @unittest.skipUnless(os.name == "nt", "Windows 隐藏属性")
    def test_windows_hidden_attribute_is_rejected_on_files_and_parents(self) -> None:
        hidden_file = self.write("secret.txt")
        visible_child = self.write("folder/visible.txt")
        set_attributes = ctypes.WinDLL("kernel32", use_last_error=True).SetFileAttributesW
        set_attributes.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        set_attributes.restype = ctypes.c_int
        for target in (hidden_file, visible_child.parent):
            previous = target.stat().st_file_attributes
            self.assertTrue(set_attributes(str(target), previous | 0x2))
            self.addCleanup(set_attributes, str(target), previous)
        self.assertEqual(artifacts.list_artifacts(17)["files"], [])
        for relative in ("secret.txt", "folder/visible.txt"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                artifacts.open_artifact(17, relative)

    def test_file_replacement_during_open_is_rejected_and_descriptor_is_closed(self) -> None:
        candidate = self.write("result.txt", b"original")
        replacement = self.directory / "replacement.txt"
        replacement.write_bytes(b"replacement")
        real_open = os.open
        opened = []

        def replace_then_open(path, flags):
            os.replace(replacement, candidate)
            fd = real_open(path, flags)
            opened.append(fd)
            return fd

        with patch.object(artifacts.os, "open", side_effect=replace_then_open):
            with self.assertRaises(OSError):
                artifacts.open_artifact(17, "result.txt")
        self.assertEqual(len(opened), 1)
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    @unittest.skipUnless(os.name == "nt", "Windows 文件句柄路径")
    def test_final_handle_path_must_match_requested_file(self) -> None:
        self.write("result.txt")
        with patch.object(artifacts, "_windows_handle_path", return_value=self.directory / "outside"):
            with self.assertRaises(OSError):
                artifacts.open_artifact(17, "result.txt")

    @staticmethod
    async def serve(response, send) -> None:
        async def receive():
            return {"type": "http.disconnect"}

        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    def test_download_headers_exact_bytes_initial_size_and_normal_close(self) -> None:
        for content in (b"", b"binary\x00content" * 10000):
            with self.subTest(size=len(content)):
                path = self.write("结果.xlsx", content)
                stream = artifacts.open_artifact(17, "结果.xlsx")
                response = artifacts.ArtifactDownloadResponse(stream, "结果.xlsx")
                with path.open("ab") as writer:
                    writer.write(b"later bytes are not part of this response")
                messages = []

                async def send(message):
                    messages.append(message)

                asyncio.run(self.serve(response, send))
                self.assertTrue(stream.closed)
                self.assertEqual(
                    b"".join(item.get("body", b"") for item in messages), content
                )
                self.assertEqual(response.headers["content-length"], str(len(content)))
                self.assertEqual(response.headers["content-type"], "application/octet-stream")
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")
                self.assertEqual(response.headers["cache-control"], "no-store")
                disposition = response.headers["content-disposition"]
                self.assertTrue(disposition.startswith("attachment;"))
                self.assertTrue(unquote(disposition).endswith("UTF-8''结果.xlsx"))
                self.assertTrue(all(len(item.get("body", b"")) <= artifacts.CHUNK_BYTES for item in messages))

    def test_download_closes_when_sending_start_or_body_fails_or_is_cancelled(self) -> None:
        self.write("result.bin", b"content")
        for failure_at, exception in (
            ("http.response.start", OSError),
            ("http.response.body", OSError),
            ("http.response.body", asyncio.CancelledError),
        ):
            with self.subTest(failure_at=failure_at, exception=exception):
                stream = artifacts.open_artifact(17, "result.bin")
                response = artifacts.ArtifactDownloadResponse(stream, "result.bin")

                async def send(message):
                    if message["type"] == failure_at:
                        raise exception()

                expected = ClientDisconnect if exception is OSError else asyncio.CancelledError
                with self.assertRaises(expected):
                    asyncio.run(self.serve(response, send))
                self.assertTrue(stream.closed)

    def test_response_setup_failure_closes_opened_stream(self) -> None:
        self.write("result.txt")
        stream = artifacts.open_artifact(17, "result.txt")
        with patch.object(artifacts.os, "fstat", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                artifacts.ArtifactDownloadResponse(stream, "result.txt")
        self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
