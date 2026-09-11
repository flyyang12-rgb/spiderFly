# 自适应选择器与页面改版
工具：scrapling
接入状态：已有受控适配；具体原生功能是否开放以正文和平台接口为准
主题：adaptive

版本：Scrapling 0.4.15；项目锁定版本
核对日期：2026-09-11
适用范围：Scrapling 原生参考；平台未配置持久自适应存储
来源：[docs/parsing/adaptive.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/parsing/adaptive.md)
来源：[docs/fetching/choosing.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/fetching/choosing.md)

关键词：自适应 adaptive auto_save 页面改版 元素定位 selector storage identifier。

## 自适应解决什么

Scrapling 可以保存已匹配元素的特征，在选择器失效后尝试寻找相似元素。需要启用自适应能力并有有效的历史存储；它不是从零推断用户想采哪个字段。默认开关、保存和匹配过程要分别配置。

## 原生配置思路

原生 Selector/Fetcher 的 adaptive 配置启用能力；CSS/XPath 调用中的 auto_save 保存已找到元素的特征，后续 adaptive 查询尝试恢复定位。storage/storage_args、域名与 identifier 等影响存储与匹配身份。修改目录、域名或清空存储可能使历史特征不可用，使用前核对固定版本的具体参数。

## 在本项目怎么回答

现有平台快照与解析没有建立可复用的持久自适应存储流程。因此可以解释其原理与接入条件，不能说“网站改版后任务已经会自动适配”。当前选择器失效先读取新 HTML/JSON 证据，重新核对字段和业务条件，再保存并验证修改版本。

## 匹配后还要验收

相似元素可能是广告、推荐卡片或不同业务字段。恢复匹配不等于语义正确，必须核对关键字段样本、主键、数量和条件。不要把自适应选择器和平台 AI 故障修复混为一谈：一个是元素匹配机制，一个是脚本验证与版本流程。
