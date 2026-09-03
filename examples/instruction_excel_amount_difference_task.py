"""上传此单文件并声明 spiderfly-instructions==0.1.3，计算上传表的两组金额差额。"""

from example_flows.excel_amount_difference import run_flow
from spiderfly_instructions.task import TaskContext, TaskResult, run_task


def process(context: TaskContext) -> TaskResult:
    result = run_flow(str(context.input_file))
    message = (
        f"{result['left_status']}合计：{result['left_total']}；"
        f"{result['right_status']}合计：{result['right_total']}；"
        f"差额：{result['difference']}"
    )
    with (context.output_dir / "金额差额.txt").open("x", encoding="utf-8") as stream:
        stream.write(message + "\n")
        stream.write(f"匹配行数：{result['left_row_count']} / {result['right_row_count']}\n")
        stream.write("指令调用：" + " → ".join(result["instruction_calls"]) + "\n")
    return TaskResult(message=message, code="AMOUNT_DIFFERENCE_DONE")


if __name__ == "__main__":
    raise SystemExit(run_task(process))

