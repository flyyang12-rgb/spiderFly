"""独立 Python 流程模板：声明 spiderfly-instructions==0.1.3，并替换 process 中的业务代码。"""

from spiderfly_instructions.task import TaskContext, TaskResult, run_task


def process(context: TaskContext) -> TaskResult:
    # context.input_file 是已完整留存的上传文件，无上传时为 None。
    # 将业务结果保存到 context.output_dir，即可从运行记录下载。
    with (context.output_dir / "运行说明.txt").open("x", encoding="utf-8") as stream:
        stream.write("任务模板已运行，请将 process 替换为你的业务流程。\n")
    return TaskResult(message="模板运行成功，已生成运行说明。")


if __name__ == "__main__":
    # 本示例不依赖上传文件；需要上传文件的业务使用默认 require_input=True。
    raise SystemExit(run_task(process, require_input=False))
