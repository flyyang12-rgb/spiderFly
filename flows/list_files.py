"""独立文件清单流程；需要 spiderfly-instructions==0.1.4。"""

from __future__ import annotations

import argparse
import json
import sys

from spiderfly_instructions import InstructionError, InstructionRegistry
from spiderfly_instructions.files import LIST_FILES
from spiderfly_instructions.task import TaskContext, TaskResult, run_task


# 平台模式填写执行任务那台机器上已有的文件夹；本地模式可通过参数传入。
FOLDER_PATH = ""
FILE_PATTERN = "*"


def get_files(folder_path: str, pattern: str = FILE_PATTERN) -> dict:
    registry = InstructionRegistry()
    registry.register(LIST_FILES)
    return registry.execute("file.list", {
        "folder_path": folder_path,
        "pattern": pattern,
    }).model_dump()


def process(context: TaskContext) -> TaskResult:
    if not FOLDER_PATH:
        raise ValueError("请先在流程顶部填写 FOLDER_PATH，使用执行机器上的文件夹路径。")
    result = get_files(FOLDER_PATH)
    with (context.output_dir / "文件清单.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return TaskResult(message=f"找到 {result['count']} 个文件，已保存文件清单。")


def main() -> int:
    if len(sys.argv) == 1:
        return run_task(process, require_input=False)
    parser = argparse.ArgumentParser(description="获取文件列表，不读取或修改文件内容")
    parser.add_argument("folder_path", help="要列出文件的文件夹")
    parser.add_argument("--pattern", default=FILE_PATTERN, help="文件名通配符，例如 *.xlsx")
    args = parser.parse_args()
    try:
        print(json.dumps(get_files(args.folder_path, args.pattern), ensure_ascii=False, indent=2))
    except InstructionError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
