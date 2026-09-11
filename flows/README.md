# 独立 Python 业务流程

业务规则集中在这里维护。每个 `.py` 都能单文件上传，不导入平台 app 或其他业务脚本。

| 文件 | 用途 |
| --- | --- |
| [template.py](template.py) | 通用任务入口，不需要上传文件 |
| [order_filter.py](order_filter.py) | 保留状态为待处理的订单，输出新 Excel |
| [pending_average.py](pending_average.py) | 为待处理行计算均值，保留原工作簿并追加列 |
| [amount_difference.py](amount_difference.py) | 两组金额汇总后相减，使用 Decimal |
| [list_files.py](list_files.py) | 列出执行机器指定目录内的文件 |

这些示例声明 `spiderfly-runtime==0.1.0`。任务平台从 `release/runtime` 安装本地 wheel；不需要使用指令集。其他普通 Python 脚本可以直接使用标准库或 openpyxl，不强制依赖运行库。

不带参数时由平台运行；均值、差额和文件列表也支持命令行参数。`list_files.py` 在平台使用前填写顶部的 `FOLDER_PATH`。输入保存为本次产物下的 `流程文件/输入.xlsx`，结果写入 `流程文件/输出`；原上传文件不修改。

[运行库说明](../backend/RUNTIME.md)解释 `TaskContext`、`TaskResult` 与 `run_task`。[项目架构](../docs/项目架构.md)说明平台、运行库与业务边界。

`examples` 中旧入口仍按各自声明的 `spiderfly-instructions` 版本运行，仅用于兼容；新流程不要复制其中的旧指令调用方式。修改这里的文件不会自动替换已经上传的任务。
