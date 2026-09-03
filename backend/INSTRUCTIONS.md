# SpiderFly 指令包

`spiderfly-instructions 0.1.4` 提供六条可在 Python 代码中组合的指令，以及通用 Python 任务入口：

- `text.join_nonempty`：合并非空文字。
- `excel.read`：读取 `.xlsx` 数据表。
- `excel.write`：把数据保存为新的 `.xlsx` 文件，也可按模板在原表右侧追加列。
- `table.filter_equals`：按某一列等于指定值筛选数据，保留全部列和原行序。
- `math.average`：计算一个数字或英文逗号分隔的多个数字的平均数。
- `file.list`：获取文件夹当前一层的文件清单，可按文件名通配符挑选。

包内还提供 `example_flows.excel_name` 姓名合并、`example_flows.excel_pending_average` 待处理平均数和 `example_flows.excel_amount_difference` 两组金额差额三个普通 Python 流程。

需要 Python 3.12 或更新版本；本次实际验证使用 Windows、Python 3.12.10。

## 安装

使用需要运行指令的那个 Python 环境，安装本地构建的 wheel 文件：

```powershell
python -m pip install "D:\包文件\spiderfly_instructions-0.1.4-py3-none-any.whl"
```

把路径替换为拿到的实际文件。包的两个直接依赖是 Pydantic 和 openpyxl，pip 会按声明安装它们及其依赖；wheel 本身不包含 Python 或第三方依赖全集。该包当前只在本地构建，未上传公开包仓库。

`table.filter_equals` 从包版本 `0.1.1` 起提供；`math.average` 和按模板追加列从包版本 `0.1.2` 起提供；通用任务入口和金额差额流程从 `0.1.3` 起提供；`file.list` 从 `0.1.4` 起提供。本次新增文件列表能力，原五条指令及运行辅助实现不变；`excel.write` 的指令版本为 `0.1.1`，其余五条指令版本为 `0.1.0`。已有 `0.1.0` 至 `0.1.3` 包继续保留，使用旧版本的任务不会自动升级。

新业务流程放在仓库的 `flows/` 目录，用到哪条指令就导入哪条；只有新增或修复公共能力才需要更新安装包。之前打包的业务示例保留兼容，新独立流程不收入公共包。

## 获取文件列表

```python
from spiderfly_instructions import InstructionRegistry
from spiderfly_instructions.files import LIST_FILES

registry = InstructionRegistry()
registry.register(LIST_FILES)
result = registry.execute("file.list", {"folder_path": ".", "pattern": "*.xlsx"})
for file_path in result.files:
    print(file_path)  # 后续可交给 excel.read 或其他流程
print(result.count)
```

`folder_path` 必填；`pattern` 可省略，默认 `"*"`。匹配文件名时忽略大小写，支持 `*`、`?`、`[abc]` 等通配符，不接受带路径的条件。只列当前层的普通文件，不进入子文件夹、不跟随文件符号链接；隐藏文件与临时文件也参与匹配。结果为按文件名排序的绝对路径 `files` 和数量 `count`，空文件夹或无匹配正常返回空列表。没有读取权限、路径不存在或把文件当文件夹均明确失败。

清单是本次扫描所得，后续文件仍可能变动；得到路径不代表后续一定能打开。全部清单一次读入内存。平台运行时，文件夹必须在实际执行机器上存在且可访问，并非浏览器所在电脑的文件夹；指令不会上传文件夹或修改其中的文件。

## 调用

```python
from spiderfly_instructions import InstructionRegistry
from spiderfly_instructions.demo import JOIN_NONEMPTY

registry = InstructionRegistry()
registry.register(JOIN_NONEMPTY)
result = registry.execute("text.join_nonempty", {"items": ["库存日报", "订单核对"]})
print(result.text)
```

筛选已经读取的数据时，传入完整列名、数据行、筛选列和值：

```python
from spiderfly_instructions import InstructionRegistry
from spiderfly_instructions.table_filter import FILTER_EQUALS

registry = InstructionRegistry()
registry.register(FILTER_EQUALS)
result = registry.execute("table.filter_equals", {
    "columns": ["订单号", "状态"],
    "rows": [
        {"订单号": "001", "状态": "待处理"},
        {"订单号": "002", "状态": "已完成"},
    ],
    "column": "状态",
    "value": "待处理",
})
print(result.rows)       # [{'订单号': '001', '状态': '待处理'}]
print(result.row_count)  # 1
```

返回完整 `columns`、筛选后的 `rows` 和 `row_count`；可直接把列和行交给不带 `template_file` 的 `excel.write`，保存成新数据表。文字精确匹配，空值与空字符串分开，布尔值不当作数字；数字 `1` 与 `1.0` 可匹配。缺列或行字段不完整会报错，无匹配返回空表。非有限数字或带时区的时间不能用作筛选值或出现在筛选列；此指令不修改原数据或文件。

计算平均数只需传一个 `value`：

```python
from spiderfly_instructions import InstructionRegistry
from spiderfly_instructions.average import AVERAGE

registry = InstructionRegistry()
registry.register(AVERAGE)
print(registry.execute("math.average", {"value": "5,2"}).model_dump())
# {'average': 3.5, 'count': 2}
print(registry.execute("math.average", {"value": 5.6}).model_dump())
# {'average': 5.6, 'count': 1}
```

`value` 必填，只接受文字、整数或小数；返回 `average`（浮点平均数）和 `count`（参与计算的数字个数）。英文逗号表示分项，不表示小数点或千位分隔符，例如 `"1,000"` 是 `1` 和 `0`，平均数为 `0.5`。各项可带首尾空格；空白项、非数字或非有限数字报 `MATH_VALUE_INVALID`，布尔值等错误类型报 `INPUT_INVALID`。不会执行文字中的代码。

## 按原工作簿追加列

`excel.write` 的可选参数 `template_file` 默认为 `None`；不填写时仍创建普通数据表，不复制源表格式。填写已有 `.xlsx` 路径后，输出仍必须是一个不存在的新文件，所在文件夹须已存在。

- `sheet_name` 默认仍为 `"数据"`；按模板写入时必须对应模板中已有的工作表，通常传入 `excel.read` 返回的 `sheet_name`。
- `columns` 必须是完整原列按原顺序排列，再接至少一列新增列；`rows` 必须包含全部原有效行和新增值。原列的值、类型及行序必须一致，不能把筛选后的部分行传进来，也不能改原列或删行。
- 原表中间的完全空行保留原位置，新增值写回对应的原行。其他工作表和原单元格的基本格式会保留，但不承诺所有 Excel 对象或功能完全保真；目标工作表中的公式和错误单元格仍不支持。

不符合模板结构会报 `EXCEL_TEMPLATE_INVALID`，传入数据与原表不一致会报 `EXCEL_TEMPLATE_MISMATCH`。源文件不会被覆盖。

## 通用 Python 任务入口

平台任务入口可以复用 `spiderfly_instructions.task` 中的 `run_task`。公共代码读取平台提供的路径、完整留存上传文件、建立输出目录，并在业务函数结束后保存成功或失败回执；它不加载平台服务器，不自动重试或检查业务输出是否正确。

```python
from spiderfly_instructions.task import TaskContext, TaskResult, run_task

def process(context: TaskContext) -> TaskResult:
    with (context.output_dir / "运行说明.txt").open("x", encoding="utf-8") as stream:
        stream.write("处理完成\n")
    return TaskResult(message="处理完成，结果可从本次文件下载。")

if __name__ == "__main__":
    raise SystemExit(run_task(process, require_input=False))
```

`context.input_file` 是留存的上传文件路径，无上传时为 `None`；`context.output_dir` 是本次 `artifacts/流程文件/输出`。默认 `require_input=True`，缺少上传文件会失败；不依赖输入文件时显式设为 `False`。回执说明最多 1000 字，结果编码最多 64 位、默认 `TASK_DONE`。业务函数必须返回 `TaskResult`，抛出异常则失败；已存在的执行回执和同次目录拒绝复用。

需要平台提供 `SPIDERFLY_RESULT_FILE`、`SPIDERFLY_ARTIFACT_DIR`；上传文件通过 `SPIDERFLY_TEMPLATE_FILE` 传入。本例需要从平台启动，普通本地调用单独的业务流程即可。已有平台入口不自动改写。

## 完整示范

安装后可以从自己的工作目录运行完整示范，无需切换到源码目录：

```powershell
python -m example_flows.excel_name --demo "新的演示目录"
```

处理自己的“状态”“金额”表格，可用以下流程。它保留全部原行，仅对状态完全等于“待处理”的行分别计算平均数，在右侧新增“待处理平均数”；其他行的新列留空。原表已有同名结果列时会报错。

```powershell
python -m example_flows.excel_pending_average "D:\表格\输入.xlsx" "D:\表格\平均数结果.xlsx" --sheet "订单"
```

路径和工作表名换成实际值；不填 `--sheet` 时读取第一张表。该流程复用 `excel.read`、`math.average`、`excel.write`，是安装包内的代码示例，不代表新增了已部署的平台任务入口。

两组金额差额可直接运行 `python -m example_flows.excel_amount_difference "输入.xlsx"`。读取和两次筛选使用已有指令，汇总和相减用 Python Decimal，返回精确十进制文字；支持 `--sheet`、`--status-column`、`--amount-column`、`--left-status`、`--right-status`，不改写输入文件。

每个任务环境需要各自安装；本包没有自动安装到 SpiderFly 已有任务。修改开发源码后，需要重新构建并安装新包，已有安装不会自动升级。
