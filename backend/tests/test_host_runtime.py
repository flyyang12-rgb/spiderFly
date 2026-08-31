from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from app.host_runtime import (
    HostBusyStatus,
    ProcessInfo,
    TemplateCopyError,
    UnsafeWorkDirectoryError,
    check_host_busy,
    cleanup_after_run,
    clear_work_directory,
    prepare_work_directory,
    validate_work_directory,
)


class HostBusyCheckTests(unittest.TestCase):
    def test_non_windows_hosts_are_idle_without_enumerating_processes(self) -> None:
        def fail_if_called():
            raise AssertionError("non-Windows must not enumerate desktop processes")

        result = check_host_busy(
            platform_name="posix",
            process_provider=fail_if_called,
            port_available_provider=lambda _port: fail_if_called(),
        )

        self.assertEqual(
            result,
            HostBusyStatus(False, "idle", "非 Windows 宿主机，无需检查 Excel 和专用浏览器端口"),
        )

    def test_any_excel_process_makes_windows_host_busy(self) -> None:
        result = check_host_busy(
            platform_name="nt",
            process_provider=lambda: [
                ProcessInfo(31, r"C:\Program Files\Microsoft Office\EXCEL.EXE"),
                ProcessInfo(32, "ShadowBot.exe"),
            ],
            port_available_provider=lambda _port: True,
        )

        self.assertTrue(result.busy)
        self.assertEqual(result.code, "desktop_resource_busy")
        self.assertEqual(result.excel_pids, (31,))
        self.assertIsNone(result.browser_port)
        self.assertIn("Excel 正在运行", result.message)

    def test_personal_chrome_and_edge_do_not_make_host_busy(self) -> None:
        result = check_host_busy(
            platform_name="nt",
            process_provider=lambda: [
                ProcessInfo(41, "chrome.exe"),
                ProcessInfo(42, "msedge.exe"),
                ProcessInfo(43, "msedgewebview2.exe"),
            ],
            port_available_provider=lambda _port: True,
        )

        self.assertFalse(result.busy)
        self.assertIsNone(result.browser_port)

    def test_reserved_browser_port_makes_host_busy(self) -> None:
        result = check_host_busy(
            platform_name="nt",
            process_provider=lambda: [],
            port_available_provider=lambda port: port != 9123,
            managed_browser_port=9123,
        )

        self.assertTrue(result.busy)
        self.assertEqual(result.excel_pids, ())
        self.assertEqual(result.browser_port, 9123)
        self.assertIn("9123", result.message)
        self.assertIn("上一次浏览器没有退出", result.message)

    def test_shadowbot_is_never_treated_as_a_busy_resource(self) -> None:
        result = check_host_busy(
            platform_name="nt",
            process_provider=lambda: [ProcessInfo(61, "ShadowBot.exe")],
            port_available_provider=lambda _port: True,
        )

        self.assertFalse(result.busy)
        self.assertEqual(result.code, "idle")

    def test_inspection_failure_fails_closed_without_raising(self) -> None:
        def broken_provider():
            raise OSError("access denied")

        result = check_host_busy(
            platform_name="nt",
            process_provider=broken_provider,
            port_available_provider=lambda _port: True,
        )

        self.assertTrue(result.busy)
        self.assertEqual(result.code, "inspection_failed")
        self.assertIn("access denied", result.message)

    def test_port_inspection_failure_fails_closed(self) -> None:
        def broken_port_check(_port: int) -> bool:
            raise OSError("socket failure")

        result = check_host_busy(
            platform_name="nt",
            process_provider=lambda: [],
            port_available_provider=broken_port_check,
        )

        self.assertTrue(result.busy)
        self.assertEqual(result.code, "inspection_failed")
        self.assertIn("socket failure", result.message)


class WorkDirectoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.data = self.root / "data"
        self.apps = self.data / "apps"
        self.envs = self.data / "envs"
        self.executions = self.data / "executions"
        self.home = self.root / "home"
        for path in (
            self.project,
            self.apps,
            self.envs,
            self.executions,
            self.home,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.protection = {
            "project_root": self.project,
            "data_dir": self.data,
            "apps_dir": self.apps,
            "envs_dir": self.envs,
            "executions_dir": self.executions,
            "user_home": self.home,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()


class WorkDirectoryValidationTests(WorkDirectoryTestCase):
    def test_rejects_drive_root(self) -> None:
        drive_root = Path(self.root.anchor)

        with self.assertRaisesRegex(UnsafeWorkDirectoryError, "磁盘根目录"):
            validate_work_directory(drive_root, **self.protection)

    def test_rejects_each_protected_directory_itself(self) -> None:
        for protected in (
            self.project,
            self.data,
            self.apps,
            self.envs,
            self.executions,
            self.home,
        ):
            with self.subTest(protected=protected):
                with self.assertRaises(UnsafeWorkDirectoryError):
                    validate_work_directory(protected, **self.protection)

    def test_rejects_a_parent_that_contains_protected_directories(self) -> None:
        with self.assertRaisesRegex(UnsafeWorkDirectoryError, "可能删除系统数据"):
            validate_work_directory(self.root, **self.protection)

    def test_rejects_subdirectories_of_managed_storage(self) -> None:
        for managed_child in (
            self.apps / "program-1",
            self.envs / "environment-1",
            self.executions / "42",
        ):
            with self.subTest(managed_child=managed_child):
                with self.assertRaises(UnsafeWorkDirectoryError):
                    validate_work_directory(managed_child, **self.protection)

    def test_allows_a_dedicated_work_child_inside_data_directory(self) -> None:
        work = self.data / "work"

        resolved = validate_work_directory(work, **self.protection)

        self.assertEqual(resolved, work.resolve())

    def test_rejects_a_work_directory_that_is_a_symbolic_link(self) -> None:
        target = self.root / "real-work"
        target.mkdir()
        link = self.root / "work-link"
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"当前 Windows 策略不允许创建符号链接：{exc}")

        with self.assertRaisesRegex(UnsafeWorkDirectoryError, "符号链接"):
            validate_work_directory(link, **self.protection)


class WorkDirectoryCleanupTests(WorkDirectoryTestCase):
    def test_clears_nested_and_read_only_content_and_leaves_directory_writable(self) -> None:
        work = self.root / "work"
        nested = work / "nested"
        nested.mkdir(parents=True)
        readonly = nested / "old.xlsx"
        readonly.write_text("old", encoding="utf-8")
        readonly.chmod(stat.S_IREAD)
        (work / "other.tmp").write_text("temporary", encoding="utf-8")

        cleaned = clear_work_directory(work, **self.protection)

        self.assertEqual(cleaned, work.resolve())
        self.assertEqual(tuple(cleaned.iterdir()), ())
        probe = cleaned / "new.xlsx"
        probe.write_text("new", encoding="utf-8")
        self.assertEqual(probe.read_text(encoding="utf-8"), "new")

    def test_removes_a_child_symlink_without_deleting_its_external_target(self) -> None:
        work = self.root / "work"
        work.mkdir()
        external = self.root / "external"
        external.mkdir()
        protected_file = external / "keep.xlsx"
        protected_file.write_text("keep", encoding="utf-8")
        link = work / "linked-folder"
        try:
            os.symlink(external, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"当前 Windows 策略不允许创建符号链接：{exc}")

        clear_work_directory(work, **self.protection)

        self.assertFalse(link.exists())
        self.assertEqual(protected_file.read_text(encoding="utf-8"), "keep")

    def test_cleanup_after_run_only_empties_the_public_directory(self) -> None:
        work = self.root / "work"
        work.mkdir()
        (work / "result.xlsx").write_bytes(b"result")
        app_file = self.apps / "main.py"
        app_file.write_text("print('safe')", encoding="utf-8")

        cleanup_after_run(work, **self.protection)

        self.assertEqual(tuple(work.iterdir()), ())
        self.assertTrue(app_file.is_file())


class TemplatePreparationTests(WorkDirectoryTestCase):
    def test_prepare_clears_old_content_and_copies_one_template(self) -> None:
        work = self.root / "work"
        work.mkdir()
        (work / "stale.xlsx").write_bytes(b"stale")
        templates = self.root / "templates"
        templates.mkdir()
        source = templates / "finance-template.xlsx"
        source.write_bytes(b"template-v1")

        cleaned, copied = prepare_work_directory(
            work,
            template_path=source,
            template_name="财务模板.xlsx",
            **self.protection,
        )

        self.assertEqual(cleaned, work.resolve())
        self.assertEqual(copied, work / "财务模板.xlsx")
        self.assertEqual(copied.read_bytes(), b"template-v1")
        self.assertEqual(source.read_bytes(), b"template-v1")
        self.assertEqual([item.name for item in work.iterdir()], ["财务模板.xlsx"])

    def test_missing_template_does_not_clear_existing_work_files(self) -> None:
        work = self.root / "work"
        work.mkdir()
        stale = work / "keep-until-validation-finishes.xlsx"
        stale.write_bytes(b"keep")

        with self.assertRaises(TemplateCopyError):
            prepare_work_directory(
                work,
                template_path=self.root / "missing.xlsx",
                **self.protection,
            )

        self.assertEqual(stale.read_bytes(), b"keep")

    def test_template_original_cannot_be_inside_the_disposable_work_directory(self) -> None:
        work = self.root / "work"
        work.mkdir()
        source = work / "original.xlsx"
        source.write_bytes(b"original")

        with self.assertRaisesRegex(TemplateCopyError, "模板原件"):
            prepare_work_directory(work, template_path=source, **self.protection)

        self.assertEqual(source.read_bytes(), b"original")

    def test_invalid_destination_name_is_rejected_before_cleanup(self) -> None:
        work = self.root / "work"
        work.mkdir()
        stale = work / "keep.xlsx"
        stale.write_bytes(b"keep")
        source = self.root / "template.xlsx"
        source.write_bytes(b"template")

        with self.assertRaisesRegex(TemplateCopyError, "文件名"):
            prepare_work_directory(
                work,
                template_path=source,
                template_name=r"nested\template.xlsx",
                **self.protection,
            )

        self.assertEqual(stale.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
