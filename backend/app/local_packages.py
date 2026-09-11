"""Install pinned local runtime packages; retain frozen instruction wheels for old tasks."""

from __future__ import annotations

import re
from pathlib import Path

from .config import PROJECT_ROOT


INSTRUCTION_WHEEL_DIR = PROJECT_ROOT / "release" / "instructions"
RUNTIME_WHEEL_DIR = PROJECT_ROOT / "release" / "runtime"
LOCAL_PACKAGES = {"spiderfly-instructions": "spiderfly_instructions", "spiderfly-runtime": "spiderfly_runtime"}
_PIN = re.compile(r"==\s*((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))")


def split_local_requirements(requirements: str) -> tuple[str, dict[str, str]]:
    """Keep ordinary requirements intact; never send our reserved name to an index."""
    public_lines: list[str] = []
    versions: dict[str, str] = {}
    for raw_line in requirements.lstrip("\ufeff").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and (line.endswith("\\") or "${" in line):
            raise ValueError("依赖清单暂不支持续行或环境变量，请每行直接填写包名和版本")
        name = re.match(r"[A-Za-z0-9_.-]+", line)
        package = re.sub(r"[-_.]+", "-", name.group()).lower() if name else ""
        if package not in LOCAL_PACKAGES:
            public_lines.append(raw_line)
            continue
        declaration = re.split(r"\s+#", line[name.end():], maxsplit=1)[0].strip()
        pin = _PIN.fullmatch(declaration)
        if not pin:
            raise ValueError(
                f"SpiderFly 本地包必须固定三段版本，例如 {package}==0.1.0；"
                "暂不支持版本范围、额外选项或条件声明"
            )
        if package in versions:
            raise ValueError(f"本地包 {package} 只能声明一次")
        versions[package] = pin.group(1)
    return "\n".join(public_lines).strip(), versions


def local_wheel(package: str, version: str) -> Path:
    """Resolve only a named release; a missing local file must not fall back to PyPI."""
    if package not in LOCAL_PACKAGES or not _PIN.fullmatch("==" + version):
        raise ValueError("SpiderFly 本地包名称或版本无效")
    root = (INSTRUCTION_WHEEL_DIR if package == "spiderfly-instructions" else RUNTIME_WHEEL_DIR).resolve()
    wheel = root / f"{LOCAL_PACKAGES[package]}-{version}-py3-none-any.whl"
    if wheel.is_symlink() or wheel.resolve().parent != root or not wheel.is_file():
        raise FileNotFoundError(
            f"本机缺少本地包 {package}=={version}，请先将对应 wheel 放到 {root}；"
            "不会从公网安装同名包"
        )
    return wheel
