# Python 任务运行库

`spiderfly-runtime==0.1.0` 是可选的 Python 辅助库。业务脚本仍可直接使用标准库、openpyxl 等依赖，不必使用此包。

- `spiderfly_runtime.task`：保存本次输入副本、提供产物目录、原子写入结果回执。
- `spiderfly_runtime.excel` / `excel_write`：读取数据表、写入新文件，校验列名与结果，保留原文件。
- `spiderfly_runtime.table_filter` / `average` / `files`：普通 Python 数据处理函数。
- `_validation`：内部数据校验与错误类型。没有指令注册、字符串调度、目录或业务示例。

```python
from spiderfly_runtime import TaskContext, TaskResult, run_task
from spiderfly_runtime.excel import read_excel

def process(context: TaskContext) -> TaskResult:
    table = read_excel(str(context.input_file), required_columns=["订单号"])
    return TaskResult(f"读取 {table.row_count} 行")

if __name__ == "__main__":
    raise SystemExit(run_task(process))
```

`run_task` 不重试、不调度、不启动服务器，也不自行验证业务含义。输入、产物和回执沿用平台环境变量。无需输入时用 `run_task(process, require_input=False)`。

文件函数通过关键字参数调用，返回具有属性和 `model_dump()` 的数据对象。具体业务条件放在 [flows](../flows/README.md)，不加入运行库。

本地 wheel 位于 `release/runtime`。平台只按显式固定版本安装，不向公网查找同名包，不自动升级已有任务。
旧 `spiderfly-instructions` 已停止源码维护；0.1.0–0.1.4 wheel 保持原样，旧任务和兼容示例继续声明原版本。

当前 WSL readonly-v1 / collection-v1 未安装本运行库。需要在这些受限环境执行的 AI 脚本继续使用其已支持的标准库和第三方依赖；不能把 Windows 任务运行成功视为受限验证通过。
