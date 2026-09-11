# DrissionPage：🌏 设置语言 / Set Language
工具：drissionpage
接入状态：仅知识参考；未接入 SpiderFly AI 执行工具和 collection-v1
主题：🌏 设置语言 / Set Language；get_start/set_lang.md
版本：用户提供的 Markdown 文档 4.1.1.2
核对日期：2026-09-11
适用范围：用户文档原生 API 参考；未进行库运行验证
资料路径：get_start/set_lang.md
资料校验：afeed61ea0cb8b893519fe2950ffe1f6123319f1ec0499b7fe73c5fddfdabaff
来源：[官方对应章节（可变网页，不保证与本地版本一致）](https://www.drissionpage.cn/get_start/set_lang/)

DrissionPage 的报错信息及提示支持中文和英文。

DrissionPage error messages and prompts are available in Chinese and English.

## ✅️ 设置方法 / Usage

```python
from DrissionPage.common import Settings

Settings.set_language('en')  # 设置为中文时，填入'zh_cn'
```
