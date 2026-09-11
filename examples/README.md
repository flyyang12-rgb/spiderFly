# 兼容示例

`instruction_*.py` 是已发布旧指令包的兼容入口，按文件内原版本声明运行。对应业务实现已从当前源码移除，仍包含在 `release/instructions` 的冻结 wheel 中。

新业务从 [flows](../flows/README.md) 开始，使用普通 Python 函数。不要同时修改新流程和旧示例维护同一份业务规则。

`drissionpage_managed_template.py` 是特殊浏览器环境模板，沿用文件内说明。
