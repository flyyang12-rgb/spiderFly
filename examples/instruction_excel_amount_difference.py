"""金额差额流程的命令行入口，需要 spiderfly-instructions 0.1.3。

原 run_flow 调用和命令行参数保留，业务实现统一放在安装包中。
"""

from example_flows.excel_amount_difference import main, run_flow


if __name__ == "__main__":
    raise SystemExit(main())
