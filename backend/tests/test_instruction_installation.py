from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import environments, instruction_packages


class InstructionRequirementTests(unittest.TestCase):
    def test_utf8_bom_does_not_hide_the_reserved_package(self) -> None:
        text = "\ufeffspiderfly-instructions==0.1.0"
        self.assertEqual(environments._safe_requirements(text), text.lstrip("\ufeff"))
        self.assertEqual(instruction_packages.split_instruction_requirement(text), ("", "0.1.0"))

    def test_pip_preprocessing_cannot_hide_the_reserved_package(self) -> None:
        for text in ("spiderfly-\\\ninstructions==0.1.0", "${PACKAGE_NAME}==0.1.0",
                     "spiderfly-${PACKAGE_SUFFIX}==0.1.0"):
            with self.subTest(text=text):
                for validate in (environments._safe_requirements,
                                 instruction_packages.split_instruction_requirement):
                    with self.assertRaisesRegex(ValueError, "暂不支持续行或环境变量"):
                        validate(text)

    def test_extracts_normalized_pinned_name_and_preserves_other_requirements(self) -> None:
        for name in ("spiderfly-instructions", "SpiderFly_Instructions", "spiderfly...instructions"):
            with self.subTest(name=name):
                source = f"requests>=2,<3\n{name} == 0.1.0 # local package\n# comment"
                public, version = instruction_packages.split_instruction_requirement(source)
                self.assertEqual(public, "requests>=2,<3\n# comment")
                self.assertEqual(version, "0.1.0")
                self.assertEqual(environments._safe_requirements(source), source)

    def test_reserved_name_cannot_fall_through_for_unsupported_declarations(self) -> None:
        for suffix in ("", ">=0.1", "==0.1.*", "===0.1.0", "==01.1.0", "[excel]==0.1.0",
                       "==0.1.0; python_version >= '3.12'", "==0.1.0 --extra-index-url x"):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(ValueError, "必须固定三段版本"):
                    environments._safe_requirements("SPIDERFLY_instructions" + suffix)
        with self.assertRaisesRegex(ValueError, "只能声明一次"):
            environments._safe_requirements("spiderfly-instructions==0.1.0\nspiderfly_instructions==0.1.0")

    def test_local_paths_remain_rejected_for_user_supplied_dependencies(self) -> None:
        for value in (r"C:\wheels\package.whl", "C:/wheels/package.whl", "./package.whl",
                      "spiderfly-instructions @ file:///C:/package.whl"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "不允许 URL、参数或本地路径"):
                    environments._safe_requirements(value)

    def test_resolves_only_requested_local_version_and_rejects_path_versions(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wheel = root / "spiderfly_instructions-0.1.0-py3-none-any.whl"
            wheel.write_bytes(b"local fixture")
            with patch.object(instruction_packages, "INSTRUCTION_WHEEL_DIR", root):
                self.assertEqual(instruction_packages.instruction_wheel("0.1.0"), wheel)
                with self.assertRaisesRegex(FileNotFoundError, "不会从公网安装同名包"):
                    instruction_packages.instruction_wheel("0.2.0")
                with self.assertRaises(ValueError):
                    instruction_packages.instruction_wheel("../0.1.0")


class InstructionEnvironmentBuildTests(unittest.TestCase):
    def _build(self, *, requirements: str, missing_wheel: bool = False,
               failed_phase: str = "") -> dict:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app_dir = root / "apps" / "1"
            app_dir.mkdir(parents=True)
            script = app_dir / "main.py"
            script.write_text("print('example')\n", encoding="utf-8")
            wheel_dir = root / "release" / "instructions"
            wheel_dir.mkdir(parents=True)
            wheel = wheel_dir / "spiderfly_instructions-0.1.0-py3-none-any.whl"
            if not missing_wheel:
                wheel.write_bytes(b"fake wheel; subprocess is mocked")
            app = {"id": 1, "revision": 1, "environment_status": "pending",
                   "script_path": str(script), "requirements_text": requirements}
            calls = []

            async def fake_run(*command, **options):
                calls.append((command, options))
                if options["phase"] == "创建虚拟环境":
                    python = environments._environment_python(Path(command[-1]))
                    python.parent.mkdir(parents=True)
                    python.write_bytes(b"fake interpreter")
                return (1, "failed verification") if options["phase"] == failed_phase else (0, "ok")

            with (
                patch.object(environments, "RPA_APPS_DIR", root / "apps"),
                patch.object(environments, "RPA_ENVS_DIR", root / "envs"),
                patch.object(environments, "BASE_PYTHON", sys.executable),
                patch.object(instruction_packages, "INSTRUCTION_WHEEL_DIR", wheel_dir),
                patch.object(environments, "fetch_one", return_value=app),
                patch.object(environments, "fetch_all", return_value=[]),
                patch.object(environments, "execute_result", return_value=(0, 1)) as updates,
                patch.object(environments, "execute") as failures,
                patch.object(environments, "_run_command", new=AsyncMock(side_effect=fake_run)),
            ):
                asyncio.run(environments.build_environment(1))
            generated = app_dir / "requirements.install.txt"
            return {"calls": calls, "updates": updates.call_args_list,
                    "failures": failures.call_args_list, "wheel": str(wheel),
                    "original": (app_dir / "requirements.txt").read_text(encoding="utf-8"),
                    "generated": generated.read_text(encoding="utf-8") if generated.exists() else None}

    def test_build_installs_exact_local_file_and_checks_actual_package_before_ready(self) -> None:
        result = self._build(requirements="spiderfly-instructions==0.1.0\nrequests>=2,<3")
        phases = [options["phase"] for _, options in result["calls"]]
        self.assertEqual(phases, ["创建虚拟环境", "安装依赖", "验证解释器", "检查依赖", "验证指令包"])
        command = result["calls"][1][0]
        self.assertEqual(command[1:5], ("-m", "pip", "install", result["wheel"]))
        self.assertNotIn("spiderfly-instructions", result["generated"])
        self.assertEqual(result["generated"].strip(), "requests>=2,<3")
        self.assertIn("spiderfly-instructions==0.1.0", result["original"])
        self.assertEqual(len(result["updates"]), 2)
        self.assertIn("SHA-256:", result["updates"][-1].args[1][1])
        self.assertEqual(result["failures"], [])

    def test_missing_wheel_stops_before_any_install_command(self) -> None:
        result = self._build(requirements="spiderfly-instructions==0.1.0", missing_wheel=True)
        self.assertEqual(result["calls"], [])
        self.assertEqual(len(result["updates"]), 1)
        self.assertIn("本机缺少指令包", result["failures"][0].args[1][0])

    def test_failed_instruction_import_does_not_publish_environment(self) -> None:
        result = self._build(requirements="spiderfly-instructions==0.1.0", failed_phase="验证指令包")
        self.assertEqual(len(result["updates"]), 1)
        self.assertIn("指令包无法导入", result["failures"][0].args[1][0])

    def test_ordinary_requirements_keep_existing_install_path(self) -> None:
        result = self._build(requirements="requests>=2,<3")
        command = result["calls"][1][0]
        self.assertEqual(command[1:5], ("-m", "pip", "install", "-r"))
        self.assertTrue(command[-1].endswith("requirements.txt"))
        self.assertIsNone(result["generated"])
        self.assertNotIn("验证指令包", [options["phase"] for _, options in result["calls"]])
        self.assertEqual(result["failures"], [])


if __name__ == "__main__":
    unittest.main()
