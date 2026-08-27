from __future__ import annotations

import argparse
import ast
import os
import re
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT.parent / "验证码转发.py"


def read_legacy_credentials(source: Path) -> tuple[str, str]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    values: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in {"App_ID", "App_Secret"}:
            continue
        value = ast.literal_eval(node.value)
        if isinstance(value, str):
            values[target.id] = value.strip()
    app_id = values.get("App_ID", "")
    app_secret = values.get("App_Secret", "")
    if not app_id or not app_secret:
        raise RuntimeError("源文件中没有找到完整的 App_ID / App_Secret")
    return app_id, app_secret


def write_private_env(app_id: str, app_secret: str, receiver: str) -> Path:
    if not re.fullmatch(r"1\d{10}", receiver):
        raise ValueError("接收手机号格式不正确")
    target = PROJECT_ROOT / ".env"
    content = (
        f"FEISHU_APP_ID={app_id}\n"
        f"FEISHU_APP_SECRET={app_secret}\n"
        f"FEISHU_RECEIVER_ID={receiver}\n"
        "FEISHU_RECEIVER_ID_TYPE=mobile\n"
    )
    file_descriptor, temp_name = tempfile.mkstemp(
        prefix=".spiderfly-env-",
        dir=PROJECT_ROOT,
        text=True,
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(temp_name, target)
    finally:
        try:
            os.remove(temp_name)
        except FileNotFoundError:
            pass
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移验证码脚本中的飞书应用配置")
    parser.add_argument("--receiver", required=True, help="飞书接收人手机号")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"找不到配置源文件：{source}")
    app_id, app_secret = read_legacy_credentials(source)
    write_private_env(app_id, app_secret, args.receiver.strip())
    masked = "*******" + args.receiver.strip()[-4:]
    print(f"FEISHU_CONFIG_IMPORT=PASS receiver={masked} type=mobile")


if __name__ == "__main__":
    main()
