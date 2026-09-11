# Scrapling 采集知识地图
工具：scrapling
接入状态：已有受控适配；具体原生功能是否开放以正文和平台接口为准
主题：overview

版本：Scrapling 0.4.15；项目锁定版本
核对日期：2026-09-11
适用范围：原生功能参考 + SpiderFly 能力对照
来源：[docs/fetching/choosing.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/fetching/choosing.md)
来源：[docs/spiders/architecture.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/spiders/architecture.md)

Scrapling 是采集与解析基础库。本项目固定 scrapling[fetchers]==0.4.15，Playwright==1.62.0。知识说明已核对源码与官方固定版本资料；不代表当前网站、浏览器或 WSL 已现场验证。
三种环境必须分开：原生 Python 库、AI 探索工具、collection-v1 定时脚本。项目脚本优先用 spiderfly_collection；原生示例用于学习，不能原样承诺能在受限脚本里执行。新版官网可能与本机固定版本不同。

## 工具能力对照

| 需求 | Scrapling 原生 | SpiderFly 当前入口 |
| --- | --- | --- |
| 静态页面 | Fetcher / FetcherSession | 探索 scrape_page(http/auto)；脚本 get/fetch |
| 动态渲染 | DynamicFetcher / DynamicSession | 探索 dynamic；脚本 render/dynamic_session |
| 无头探索与连续交互 | StealthyFetcher / AsyncStealthySession | scrape_page(stealth)、scrape_live_open/act |
| CSS / XPath | Selector | 快照提取与脚本返回的 Selector |
| 翻页和去重 | 自行组织请求或 Spider | DOM/JSON 探索工具；脚本 crawl |
| 并发与断点 | Spider 引擎有独立机制 | 平台 crawl 的 1–4 并发和受管断点 |
| 自适应选择器 | adaptive 与存储配置 | 知识可解释；平台未配置持久自适应存储 |
| 代理池、完整 Spider、CLI、MCP | 原生参考能力 | 不视为现有 AI 工具或 collection-v1 已开放 |

## 按问题选章节

选择器与字段 → selectors.md；HTTP → http.md；动态和等待 → dynamic.md；Cookie 与登录 → sessions.md；分页/JSON/去重 → pagination.md；并发与续采 → concurrency.md；改版 → adaptive.md；Spider → spiders.md；代理与限流 → proxies.md；报错 → troubleshooting.md；脚本例子 → recipes.md；网页知识库/MCP/CLI → rag.md。

## 回答与交付依据

解释“是什么、区别、怎么用”时引用知识与官方来源即可。实际采集必须另查真实页面或响应：站点选择器、接口字段、分页参数和结果条数不是知识库能预先保证的。生成脚本必须保存草稿并试跑；已保存任务正常执行不重新调用模型。采集结果写入本次产物目录。
