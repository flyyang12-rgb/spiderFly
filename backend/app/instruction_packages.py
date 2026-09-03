"""Resolve explicitly pinned SpiderFly instructions from the local release folder."""

from __future__ import annotations

import re
from pathlib import Path

from .config import PROJECT_ROOT


INSTRUCTION_WHEEL_DIR = PROJECT_ROOT / "release" / "instructions"
_PIN = re.compile(r"==\s*((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))")


def split_instruction_requirement(requirements: str) -> tuple[str, str | None]:
    """Keep ordinary requirements intact; never send our reserved name to an index."""
    public_lines: list[str] = []
    version: str | None = None
    for raw_line in requirements.lstrip("\ufeff").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and (line.endswith("\\") or "${" in line):
            raise ValueError("依赖清单暂不支持续行或环境变量，请每行直接填写包名和版本")
        name = re.match(r"[A-Za-z0-9_.-]+", line)
        if not name or re.sub(r"[-_.]+", "-", name.group()).lower() != "spiderfly-instructions":
            public_lines.append(raw_line)
            continue
        declaration = re.split(r"\s+#", line[name.end():], maxsplit=1)[0].strip()
        pin = _PIN.fullmatch(declaration)
        if not pin:
            raise ValueError(
                "SpiderFly 指令包必须固定三段版本，例如 spiderfly-instructions==0.1.0；"
                "暂不支持版本范围、额外选项或条件声明"
            )
        if version is not None:
            raise ValueError("SpiderFly 指令包只能声明一次")
        version = pin.group(1)
    return "\n".join(public_lines).strip(), version


def instruction_wheel(version: str) -> Path:
    """Resolve only a named release; a missing local file must not fall back to PyPI."""
    if not _PIN.fullmatch("==" + version):
        raise ValueError("SpiderFly 指令包版本无效")
    root = INSTRUCTION_WHEEL_DIR.resolve()
    wheel = root / f"spiderfly_instructions-{version}-py3-none-any.whl"
    if wheel.is_symlink() or wheel.resolve().parent != root or not wheel.is_file():
        raise FileNotFoundError(
            f"本机缺少指令包 {version}，请先将对应 wheel 放到 release/instructions；"
            "不会从公网安装同名包"
        )
    return wheel
