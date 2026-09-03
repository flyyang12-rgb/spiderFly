# 用 Python 流程计算两组金额之差

本页保留之前的测试与兼容入口。当前优先使用[独立金额差额流程](../flows/amount_difference.py)：规则直接放在该文件内，只引用公共指令和运行辅助。新增流程的方法见[独立流程说明](../flows/README.md)，无需为每条业务重新打包。

[命令行入口](../examples/instruction_excel_amount_difference.py)调用包内的[金额差额流程](../backend/example_flows/excel_amount_difference.py)，读取 Excel，分别筛出两个状态，将两组金额汇总后相减。默认计算“待处理总金额 − 已完成总金额”，当前源码需要 `spiderfly-instructions 0.1.3`，输入文件只读。平台上传、运行及结果下载见[Python 任务模板](Python任务模板.md)。

## 本次 ccc.xlsx 的结果

沿用已确认的样表规则，英文逗号表示多个数：`5,2` 是 5 和 2，不是 5.2 或千位格式。

| 项目 | 计算 | 结果 |
| --- | --- | --- |
| 待处理合计 | 5 + 2 + 5.6 | 12.6 |
| 已完成合计 | 12.3 | 12.3 |
| 两者之差 | 12.6 − 12.3 | 0.3 |

## 怎样运行

在已安装指令包的 Python 环境中，把路径替换为实际位置：

```powershell
python "D:\项目\spiderFly\examples\instruction_excel_amount_difference.py" "C:\Users\20898\Desktop\ccc.xlsx"
```

默认读取第一张表，使用“状态”和“金额”两列。可以通过 `--sheet`、`--status-column`、`--amount-column`、`--left-status`、`--right-status` 更换工作表、列名和两组状态，无需修改流程。例如：

```powershell
python "D:\项目\spiderFly\examples\instruction_excel_amount_difference.py" "订单.xlsx" --status-column "进度" --amount-column "货款" --left-status "未发货" --right-status "已发货"
```

左组是被减数，右组是减数；金额合计和差额会以精确十进制文字输出到 JSON，不写回 Excel。无匹配记录的一组合计为 0；其他状态不参与计算，文字条件精确匹配。参与计算的空金额、空项、布尔值、非数字或非有限数会报错，不跳过错误记录。

## 指令与流程分别负责什么

实际调用顺序是 `excel.read → table.filter_equals → table.filter_equals`。读取和两次筛选复用已有封装；两组合计和相减使用 Python 标准库 `Decimal`，不新增求和或减法指令。指令仍为五条；当前流程收入 0.1.3 包，旧 0.1.2 包保留。

十进制计算使用 28 位有效数字；如果计算会丢失有效数字或超出范围，就明确报错。业务实现只有包内一份，命令行和新平台入口都调用它；原命令行参数及 `run_flow` 调用方式保留，当前入口需要 0.1.3。

## 已完成验证

2026-09-03 在仓库外使用已安装的 0.1.2 包运行原始 ccc.xlsx，独立记录实际指令调用，读取 3 行，两次筛选分别得到 2 行和 1 行，合计与差额符合上表。原文件 SHA-256 未变；本次计算没有写回单元格。

[6 项针对性测试](../backend/tests/test_amount_difference_flow.py)全部通过，覆盖小数精度、空组、负差额、英文逗号、精确状态匹配、改列名及状态、非法金额、缺列和命令行退出结果。这是最初 0.1.2 环境的测试记录，当时未升级包或创建平台任务。随后迁入 0.1.3 包，六项检查再次通过；隔离平台两次正常运行得到 0.3，非法金额正确失败，输入和结果可下载。详见[任务模板](Python任务模板.md)及[维护记录](维护约定与决策记录.md)。

