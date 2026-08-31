from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database, environments


class PythonUploadValidationTests(unittest.TestCase):
    def test_accepts_valid_utf8_python(self) -> None:
        filename, source = environments.validate_python_upload(
            "main.py", "print('你好')\n".encode("utf-8")
        )

        self.assertEqual(filename, "main.py")
        self.assertEqual(source, "print('你好')\n")

    def test_accepts_utf8_bom_without_leaving_it_in_source(self) -> None:
        filename, source = environments.validate_python_upload(
            "bom.py", b"\xef\xbb\xbfvalue = 1\n"
        )

        self.assertEqual(filename, "bom.py")
        self.assertEqual(source, "value = 1\n")

    def test_rejects_non_python_or_nested_filenames(self) -> None:
        invalid_names = (
            "main.txt",
            "../main.py",
            "folder/main.py",
            "folder\\main.py",
            "",
        )

        for filename in invalid_names:
            with self.subTest(filename=filename):
                with self.assertRaisesRegex(ValueError, "只能上传单个 .py 文件"):
                    environments.validate_python_upload(filename, b"pass\n")

    def test_rejects_empty_and_oversized_files(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能为空"):
            environments.validate_python_upload("main.py", b"")

        with patch.object(environments, "MAX_SCRIPT_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "不能超过 2MB"):
                environments.validate_python_upload("main.py", b"12345")

    def test_rejects_non_utf8_content(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须使用 UTF-8 编码"):
            environments.validate_python_upload("main.py", b"\xff\xfe\x00\x00")

    def test_rejects_python_syntax_error_with_line_number(self) -> None:
        with self.assertRaisesRegex(ValueError, "Python 语法检查失败：第 1 行"):
            environments.validate_python_upload("main.py", b"if True print('bad')\n")


class RequirementsValidationTests(unittest.TestCase):
    def test_accepts_pypi_packages_versions_extras_and_comments(self) -> None:
        raw = (
            "  requests>=2.31,<3\r\n"
            "pandas[excel]==2.2.3\r\n"
            "# install browser support separately\r\n"
            "DrissionPage~=4.1  "
        )

        result = environments._safe_requirements(raw)

        self.assertEqual(
            result,
            "requests>=2.31,<3\npandas[excel]==2.2.3\n"
            "# install browser support separately\nDrissionPage~=4.1",
        )

    def test_accepts_an_empty_requirements_list(self) -> None:
        self.assertEqual(environments._safe_requirements("  \r\n\t"), "")

    def test_rejects_pip_options_urls_direct_references_and_paths(self) -> None:
        invalid_lines = (
            "--index-url https://example.invalid/simple",
            "-r shared-requirements.txt",
            "https://example.invalid/package.whl",
            "git+https://example.invalid/repository.git",
            "package @ https://example.invalid/package.whl",
            "/srv/wheels/package.whl",
            "\\\\server\\share\\package.whl",
            ".\\local-package",
            "../local-package",
        )

        for line in invalid_lines:
            with self.subTest(line=line):
                with self.assertRaisesRegex(ValueError, "只允许填写 PyPI 包名和版本"):
                    environments._safe_requirements(line)

    def test_comments_do_not_trigger_the_restricted_content_checks(self) -> None:
        comment = "# --index-url https://example.invalid/simple"
        self.assertEqual(environments._safe_requirements(comment), comment)

    def test_rejects_requirements_text_above_the_size_limit(self) -> None:
        with patch.object(environments, "MAX_REQUIREMENTS_CHARS", 5):
            with self.assertRaisesRegex(ValueError, "依赖清单过长"):
                environments._safe_requirements("package")


class EnvironmentRuntimeTests(unittest.TestCase):
    def test_active_environment_revision_comes_from_the_published_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_dir = root / "app_7_r3_1234abcd"
            env_dir.mkdir()
            app = {"id": 7, "env_path": str(env_dir), "revision": 4}
            with patch.object(environments, "RPA_ENVS_DIR", root):
                self.assertEqual(environments.active_environment_revision(app), 3)

    @staticmethod
    def _create_runtime(apps_root: Path, envs_root: Path) -> tuple[Path, Path]:
        app_dir = apps_root / "1"
        app_dir.mkdir(parents=True)
        script = app_dir / "main.py"
        script.write_text("print('ok')\n", encoding="utf-8")
        env_dir = envs_root / "app_1_r1_1234abcd"
        python_path = environments._environment_python(env_dir)
        python_path.parent.mkdir(parents=True)
        python_path.write_bytes(b"runtime")
        return script, env_dir

    def test_runtime_ready_uses_an_existing_managed_environment_regardless_of_build_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apps_root = root / "apps"
            envs_root = root / "envs"
            script, env_dir = self._create_runtime(apps_root, envs_root)
            with (
                patch.object(environments, "RPA_APPS_DIR", apps_root),
                patch.object(environments, "RPA_ENVS_DIR", envs_root),
            ):
                for status in ("pending", "building", "failed"):
                    with self.subTest(status=status):
                        app = {
                            "script_path": str(script),
                            "env_path": str(env_dir),
                            "environment_status": status,
                        }
                        self.assertTrue(environments.runtime_ready(app))
                        with patch.object(environments, "fetch_one", return_value=app):
                            _, resolved_script, resolved_python = environments.app_runtime(1)
                        self.assertEqual(resolved_script, script.resolve())
                        self.assertEqual(
                            resolved_python,
                            environments._environment_python(env_dir).resolve(),
                        )

    def test_runtime_ready_rejects_paths_outside_managed_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apps_root = root / "apps"
            envs_root = root / "envs"
            apps_root.mkdir()
            envs_root.mkdir()
            outside_script = root / "outside.py"
            outside_script.write_text("pass\n", encoding="utf-8")
            with (
                patch.object(environments, "RPA_APPS_DIR", apps_root),
                patch.object(environments, "RPA_ENVS_DIR", envs_root),
            ):
                self.assertFalse(
                    environments.runtime_ready(
                        {"script_path": str(outside_script), "env_path": str(root)}
                    )
                )

    def test_unpublished_candidate_cleanup_is_scoped_and_protects_active_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            envs_root = root / "envs"
            envs_root.mkdir()
            candidate = envs_root / "app_7_r3_1234abcd"
            candidate.mkdir()
            (candidate / "partial.txt").write_text("partial", encoding="utf-8")
            with patch.object(environments, "RPA_ENVS_DIR", envs_root):
                self.assertFalse(
                    environments._remove_candidate_environment(
                        candidate,
                        app_id=7,
                        revision=3,
                        active_env_path=str(candidate),
                    )
                )
                self.assertTrue(candidate.exists())
                self.assertTrue(
                    environments._remove_candidate_environment(
                        candidate, app_id=7, revision=3
                    )
                )
                self.assertFalse(candidate.exists())

            outside = root / "app_7_r3_87654321"
            outside.mkdir()
            with patch.object(environments, "RPA_ENVS_DIR", envs_root):
                self.assertFalse(
                    environments._remove_candidate_environment(
                        outside, app_id=7, revision=3
                    )
                )
            self.assertTrue(outside.exists())

    def test_cleanup_error_does_not_escape_environment_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apps_root = root / "apps"
            envs_root = root / "envs"
            app_dir = apps_root / "1"
            app_dir.mkdir(parents=True)
            script = app_dir / "main.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            app = {
                "id": 1,
                "revision": 1,
                "environment_status": "pending",
                "script_path": str(script),
                "requirements_text": "",
            }
            with (
                patch.object(environments, "RPA_APPS_DIR", apps_root),
                patch.object(environments, "RPA_ENVS_DIR", envs_root),
                patch.object(environments, "BASE_PYTHON", root / "missing-python.exe"),
                patch.object(
                    environments,
                    "fetch_one",
                    side_effect=[app, RuntimeError("temporary cleanup db error")],
                ),
                patch.object(environments, "execute_result", return_value=(0, 1)),
                patch.object(environments, "execute"),
                self.assertLogs(environments.logger, level="ERROR") as captured,
            ):
                asyncio.run(environments.build_environment(1))

        self.assertTrue(any("清理应用 1" in line for line in captured.output))

    def test_build_environment_variables_do_not_expose_service_secrets(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SPIDERFLY_TEST_SECRET": "hidden",
                "FEISHU_TEST_SECRET": "hidden",
                "PIP_INDEX_URL": "https://packages.example.invalid/simple",
            },
            clear=True,
        ):
            result = environments._build_environment_variables()
        self.assertNotIn("SPIDERFLY_TEST_SECRET", result)
        self.assertNotIn("FEISHU_TEST_SECRET", result)
        self.assertEqual(
            result["PIP_INDEX_URL"], "https://packages.example.invalid/simple"
        )
        self.assertEqual(result["PIP_NO_INPUT"], "1")


class ManagedAppLifecycleTests(unittest.TestCase):
    @staticmethod
    def _prepare_database(root: Path) -> tuple[Path, Path, Path, int]:
        data_dir = root / "data"
        apps_root = data_dir / "apps"
        envs_root = data_dir / "envs"
        db_path = data_dir / "spiderfly.db"
        database.init_db()
        database.execute("DELETE FROM executions")
        database.execute("DELETE FROM tasks")
        database.execute("DELETE FROM rpa_apps")
        now = database.utc_now()
        user_id = database.execute(
            """
            INSERT INTO users (
                username, display_name, password_hash, role, active,
                must_change_password, created_at, updated_at
            ) VALUES ('admin-test', '测试管理员', 'not-used', 'admin', 1, 0, ?, ?)
            """,
            (now, now),
        )
        return apps_root, envs_root, db_path, user_id

    def test_cleanup_old_environments_preserves_current_and_active_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            envs_root = root / "envs"
            envs_root.mkdir()
            current = envs_root / "app_7_r3_1234abcd"
            active = envs_root / "app_7_r2_87654321"
            stale = envs_root / "app_7_r1_a1b2c3d4"
            unrelated = envs_root / "app_8_r1_11111111"
            for item in (current, active, stale, unrelated):
                environments._environment_python(item).parent.mkdir(parents=True)
                environments._environment_python(item).write_bytes(b"python")

            with patch.object(environments, "RPA_ENVS_DIR", envs_root):
                removed = environments.cleanup_old_environments(
                    7,
                    str(current),
                    (str(environments._environment_python(active)),),
                )

            self.assertEqual(removed, (str(stale.resolve()),))
            self.assertTrue(current.exists())
            self.assertTrue(active.exists())
            self.assertFalse(stale.exists())
            self.assertTrue(unrelated.exists())

    def test_archiving_unused_app_cleans_storage_and_allows_same_name_reupload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            apps_root = data_dir / "apps"
            envs_root = data_dir / "envs"
            db_path = data_dir / "spiderfly.db"
            with (
                patch.object(database, "DATA_DIR", data_dir),
                patch.object(database, "DB_PATH", db_path),
                patch.object(database, "RPA_APPS_DIR", apps_root),
                patch.object(database, "RPA_ENVS_DIR", envs_root),
                patch.object(environments, "RPA_APPS_DIR", apps_root),
                patch.object(environments, "RPA_ENVS_DIR", envs_root),
            ):
                apps_root, envs_root, _, user_id = self._prepare_database(root)
                created = environments.create_managed_app(
                    "财务报表", "main.py", b"print('ok')\n", "", user_id
                )
                app_id = int(created["id"])
                first = envs_root / f"app_{app_id}_r1_1234abcd"
                second = envs_root / f"app_{app_id}_r2_87654321"
                first.mkdir(parents=True)
                second.mkdir(parents=True)
                database.execute(
                    """
                    UPDATE rpa_apps
                    SET env_path = ?, environment_status = 'ready', revision = 2
                    WHERE id = ?
                    """,
                    (str(second), app_id),
                )

                result = environments.archive_managed_app(app_id, user_id)
                archived = database.fetch_one(
                    "SELECT * FROM rpa_apps WHERE id = ?", (app_id,)
                )
                with self.assertRaisesRegex(FileNotFoundError, "已经移除"):
                    environments.app_runtime(app_id)
                recreated = environments.create_managed_app(
                    "财务报表", "new_main.py", b"print('new')\n", "", user_id
                )

                self.assertEqual(result["cleanup_warning"], "")
                self.assertGreaterEqual(result["removed_directory_count"], 3)
                self.assertEqual(archived["archived"], 1)
                self.assertEqual(archived["environment_status"], "removed")
                self.assertFalse(first.exists())
                self.assertFalse(second.exists())
                self.assertEqual(int(recreated["id"]), app_id)
                self.assertEqual(recreated["archived"], 0)
                self.assertTrue(Path(recreated["script_path"]).is_file())

    def test_archiving_app_with_active_task_is_blocked_without_touching_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            apps_root = data_dir / "apps"
            envs_root = data_dir / "envs"
            db_path = data_dir / "spiderfly.db"
            with (
                patch.object(database, "DATA_DIR", data_dir),
                patch.object(database, "DB_PATH", db_path),
                patch.object(database, "RPA_APPS_DIR", apps_root),
                patch.object(database, "RPA_ENVS_DIR", envs_root),
                patch.object(environments, "RPA_APPS_DIR", apps_root),
                patch.object(environments, "RPA_ENVS_DIR", envs_root),
            ):
                apps_root, envs_root, _, user_id = self._prepare_database(root)
                created = environments.create_managed_app(
                    "共享程序", "main.py", b"print('ok')\n", "", user_id
                )
                app_id = int(created["id"])
                env_dir = envs_root / f"app_{app_id}_r1_1234abcd"
                env_dir.mkdir(parents=True)
                database.execute(
                    "UPDATE rpa_apps SET env_path = ?, environment_status = 'ready' WHERE id = ?",
                    (str(env_dir), app_id),
                )
                now = database.utc_now()
                database.execute(
                    """
                    INSERT INTO tasks (
                        name, app_id, app_name, script_path, python_path,
                        enabled, archived, created_at, updated_at
                    ) VALUES ('仍在使用', ?, '共享程序', ?, '', 1, 0, ?, ?)
                    """,
                    (app_id, created["script_path"], now, now),
                )

                with self.assertRaisesRegex(RuntimeError, "有效任务"):
                    environments.archive_managed_app(app_id, user_id)
                active = database.fetch_one(
                    "SELECT archived FROM rpa_apps WHERE id = ?", (app_id,)
                )

                self.assertEqual(active["archived"], 0)
                self.assertTrue((apps_root / str(app_id)).is_dir())
                self.assertTrue(env_dir.is_dir())

    def test_archiving_blocks_preparing_app_and_active_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            apps_root = data_dir / "apps"
            envs_root = data_dir / "envs"
            db_path = data_dir / "spiderfly.db"
            with (
                patch.object(database, "DATA_DIR", data_dir),
                patch.object(database, "DB_PATH", db_path),
                patch.object(database, "RPA_APPS_DIR", apps_root),
                patch.object(database, "RPA_ENVS_DIR", envs_root),
                patch.object(environments, "RPA_APPS_DIR", apps_root),
                patch.object(environments, "RPA_ENVS_DIR", envs_root),
            ):
                _, envs_root, _, user_id = self._prepare_database(root)
                created = environments.create_managed_app(
                    "运行保护", "main.py", b"print('ok')\n", "", user_id
                )
                app_id = int(created["id"])

                with self.assertRaisesRegex(RuntimeError, "正在准备"):
                    environments.archive_managed_app(app_id, user_id)

                env_dir = envs_root / f"app_{app_id}_r1_1234abcd"
                python_path = environments._environment_python(env_dir)
                python_path.parent.mkdir(parents=True)
                python_path.write_bytes(b"python")
                database.execute(
                    "UPDATE rpa_apps SET env_path = ?, environment_status = 'ready' WHERE id = ?",
                    (str(env_dir), app_id),
                )
                now = database.utc_now()
                task_id = database.execute(
                    """
                    INSERT INTO tasks (
                        name, app_id, app_name, script_path, python_path,
                        enabled, archived, created_at, updated_at
                    ) VALUES ('旧任务', ?, '运行保护', ?, ?, 0, 1, ?, ?)
                    """,
                    (app_id, created["script_path"], str(python_path), now, now),
                )
                database.execute(
                    """
                    INSERT INTO executions (
                        task_id, status, script_path_snapshot,
                        python_path_snapshot, created_at
                    ) VALUES (?, 'running', ?, ?, ?)
                    """,
                    (task_id, created["script_path"], str(python_path), now),
                )

                with self.assertRaisesRegex(RuntimeError, "排队或运行"):
                    environments.archive_managed_app(app_id, user_id)
                app = database.fetch_one(
                    "SELECT archived FROM rpa_apps WHERE id = ?", (app_id,)
                )

                self.assertEqual(app["archived"], 0)
                self.assertTrue((apps_root / str(app_id)).is_dir())
                self.assertTrue(env_dir.is_dir())


class EnvironmentCommandTests(unittest.TestCase):
    def test_run_command_returns_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            code, output = asyncio.run(
                environments._run_command(
                    sys.executable,
                    "-c",
                    "print('environment-ok')",
                    cwd=Path(temp_dir),
                    timeout_seconds=10,
                    phase="测试命令",
                )
            )
        self.assertEqual(code, 0)
        self.assertIn("environment-ok", output)

    def test_run_command_terminates_the_process_tree_after_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            code, output = asyncio.run(
                environments._run_command(
                    sys.executable,
                    "-c",
                    "import time; time.sleep(10)",
                    cwd=Path(temp_dir),
                    timeout_seconds=0.1,
                    phase="超时测试",
                )
            )
        self.assertEqual(code, 124)
        self.assertIn("已请求终止本次构建进程树", output)


if __name__ == "__main__":
    unittest.main()
