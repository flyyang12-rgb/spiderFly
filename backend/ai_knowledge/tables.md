# Excel 与 Python 处理

核对日期：2026-09-11。新业务直接编写 Python；不创建指令、注册表或字符串调度器。
AI 受限脚本继续使用环境已支持的标准库、openpyxl 等依赖。

使用 openpyxl>=3.1.5,<4 时，load_workbook(input_path) 读取；Workbook() 创建；worksheet.iter_rows(values_only=True) 遍历。
表头必须检查必需列是否存在。金额使用 Decimal(str(value))，订单号按字符串保留前导零。
不要把缺失值、零和空字符串混为一谈。筛选列/状态/求和列用顶部参数常量，并说明默认值。
input_path 来自 SPIDERFLY_TEMPLATE_FILE；未提供时明确报错，不回退到本机用户文档或虚构样表。
原文件不覆盖，结果写入 SPIDERFLY_ARTIFACT_DIR；完成后回读结果，核对行数与实际计算结果。
公式单元格：data_only=True 读取缓存，不能保证公式已重新计算。需要公式重算时应解释环境需求。
运行报告用实际读取行数、匹配行数、汇总值；代码生成和语法检查不能替代真实表格验证。

脚本按业务校验结果保存 Excel，并在 finally 中关闭自己打开的工作簿；使用桌面 Excel 时还需退出本次独立创建的应用。不要把 openpyxl 的工作簿操作描述为桌面 Excel 窗口操作。
