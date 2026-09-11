"""订单筛选：保留待处理行，依赖 spiderfly-runtime==0.1.0。"""
from spiderfly_runtime import TaskContext, TaskResult, run_task
from spiderfly_runtime.excel import read_excel
from spiderfly_runtime.excel_write import write_excel
from spiderfly_runtime.table_filter import filter_equals

STATUS_COLUMN = "状态"
TARGET_STATUS = "待处理"


def process(context: TaskContext) -> TaskResult:
    table = read_excel(str(context.input_file), required_columns=[STATUS_COLUMN])
    selected = filter_equals(table.columns, table.rows, STATUS_COLUMN, TARGET_STATUS)
    write_excel(str(context.output_dir / "结果.xlsx"), selected.columns, selected.rows,
                sheet_name=table.sheet_name)
    return TaskResult(f"读取 {table.row_count} 行，保留 {selected.row_count} 行。", "EXCEL_FILTER_DONE")


if __name__ == "__main__":
    raise SystemExit(run_task(process))
