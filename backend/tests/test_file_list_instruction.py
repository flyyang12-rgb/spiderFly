from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from spiderfly_instructions import InstructionError, InstructionRegistry
from spiderfly_instructions.files import LIST_FILES


class FileListTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.registry = InstructionRegistry()
        self.registry.register(LIST_FILES)
        for name in ("b.xlsx", "A.XLSX", "说明.txt", ".hidden", "~$草稿.xlsx"):
            (self.folder / name).write_text(name, encoding="utf-8")
        (self.folder / "子目录.xlsx").mkdir()
        (self.folder / "子目录.xlsx" / "nested.xlsx").write_text("nested", encoding="utf-8")

    def execute(self, **kwargs):
        return self.registry.execute("file.list", {"folder_path": str(self.folder), **kwargs})

    def test_all_files_are_sorted_absolute_and_contents_unchanged(self):
        before = {str(p): p.read_bytes() for p in self.folder.rglob("*") if p.is_file()}
        result = self.execute()
        self.assertEqual(result.files, [str(self.folder / name) for name in (
            ".hidden", "A.XLSX", "b.xlsx", "~$草稿.xlsx", "说明.txt",
        )])
        self.assertEqual(result.count, 5)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.folder.rglob("*") if p.is_file()})

    def test_name_patterns_ignore_case_and_never_recurse(self):
        for pattern, names in (
            ("*.xlsx", ["A.XLSX", "b.xlsx", "~$草稿.xlsx"]),
            ("?.XLSX", ["A.XLSX", "b.xlsx"]),
            ("[ab].xlsx", ["A.XLSX", "b.xlsx"]),
            ("说明.txt", ["说明.txt"]),
        ):
            with self.subTest(pattern=pattern):
                result = self.execute(pattern=pattern)
                self.assertEqual(result.files, [str(self.folder / name) for name in names])
                self.assertEqual(result.count, len(names))

    def test_empty_folder_and_no_match_are_successful_empty_lists(self):
        empty = self.folder / "empty"
        empty.mkdir()
        self.assertEqual(self.execute(folder_path=str(empty)).model_dump(), {"files": [], "count": 0})
        self.assertEqual(self.execute(pattern="*.pdf").model_dump(), {"files": [], "count": 0})

    def test_relative_folder_uses_current_working_directory(self):
        previous = Path.cwd()
        try:
            os.chdir(self.folder.parent)
            self.assertEqual(self.execute(folder_path=self.folder.name).files, self.execute().files)
        finally:
            os.chdir(previous)

    def test_missing_folder_and_file_in_place_of_folder_fail(self):
        for path, code in (
            (self.folder / "missing", "FILE_FOLDER_NOT_FOUND"),
            (self.folder / "b.xlsx", "FILE_FOLDER_INVALID"),
        ):
            with self.subTest(path=path), self.assertRaises(InstructionError) as caught:
                self.execute(folder_path=str(path))
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.instruction_id, "file.list")

    def test_invalid_inputs_do_not_access_the_filesystem(self):
        payloads = [{}, {"folder_path": str(self.folder), "recursive": True}]
        payloads += [{"folder_path": value} for value in (None, True, 1, self.folder, "", "  ", "a\x00b")]
        payloads += [{"folder_path": str(self.folder), "pattern": value}
                     for value in (None, 1, "", "  ", "\x00", "../*.xlsx", "sub\\*")]
        with patch("spiderfly_instructions.files.os.scandir") as scan:
            for payload in payloads:
                with self.subTest(payload=payload), self.assertRaises(InstructionError) as caught:
                    self.registry.execute("file.list", payload)
                self.assertEqual(caught.exception.code, "INPUT_INVALID")
            scan.assert_not_called()

    def test_access_and_disk_errors_are_explicit_not_empty_success(self):
        for error, code in ((PermissionError(), "FILE_ACCESS_DENIED"), (OSError(), "FILE_LIST_FAILED")):
            with patch("spiderfly_instructions.files.os.scandir", side_effect=error) as scan:
                with self.assertRaises(InstructionError) as caught:
                    self.execute()
                self.assertEqual(caught.exception.code, code)
                scan.assert_called_once()

    def test_interrupted_scan_does_not_return_a_partial_list(self):
        entry = Mock(name="entry")
        entry.name, entry.path = "a.txt", str(self.folder / "a.txt")
        entry.is_file.return_value = True

        def interrupted():
            yield entry
            raise PermissionError("changed during scan")

        with patch("spiderfly_instructions.files.os.scandir") as scan:
            scan.return_value.__enter__.return_value = interrupted()
            with self.assertRaises(InstructionError) as caught:
                self.execute()
            self.assertEqual(caught.exception.code, "FILE_ACCESS_DENIED")
            scan.return_value.__exit__.assert_called_once()

    def test_entry_checks_do_not_follow_file_links(self):
        entry = Mock(name="link")
        entry.name, entry.path = "linked.xlsx", str(self.folder / "linked.xlsx")
        entry.is_file.side_effect = lambda *, follow_symlinks: follow_symlinks
        with patch("spiderfly_instructions.files.os.scandir") as scan:
            scan.return_value.__enter__.return_value = iter([entry])
            self.assertEqual(self.execute().files, [])
            entry.is_file.assert_called_once_with(follow_symlinks=False)

    def test_wrong_count_duplicates_order_or_paths_fail_verification(self):
        a, b = str(self.folder / "A.XLSX"), str(self.folder / "b.xlsx")
        cases = [([a], 2), ([a, a], 2), ([b, a], 2), (["b.xlsx"], 1),
                 ([str(self.folder.parent / "out.xlsx")], 1),
                 ([str(self.folder / "说明.txt")], 1)]
        for files, count in cases:
            with self.subTest(files=files, count=count):
                handler = Mock(return_value={"files": files, "count": count})
                registry = InstructionRegistry()
                registry.register(replace(LIST_FILES, handler=handler))
                with self.assertRaises(InstructionError) as caught:
                    registry.execute("file.list", {"folder_path": str(self.folder), "pattern": "*.xlsx"})
                self.assertEqual(caught.exception.code, "VERIFICATION_FAILED")
                handler.assert_called_once()


if __name__ == "__main__":
    unittest.main()
