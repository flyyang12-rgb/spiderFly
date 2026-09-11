# Spider 爬虫框架与采集任务设计
工具：scrapling
接入状态：已有受控适配；具体原生功能是否开放以正文和平台接口为准
主题：spiders

版本：Scrapling 0.4.15；项目锁定版本
核对日期：2026-09-11
适用范围：原生概念参考；平台未开放完整 Spider 运行入口
来源：[docs/spiders/getting-started.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/spiders/getting-started.md)
来源：[docs/spiders/sessions.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/spiders/sessions.md)
来源：[docs/spiders/architecture.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/spiders/architecture.md)

关键词：Spider Request Response parse start_urls yield 爬虫框架 调度 数据管道。

## Spider 与 Fetcher 的区别

Fetcher 负责取一次页面；Spider 组织起始请求、后续请求、解析回调、会话和并发等完整采集流程。数据项与下一批请求由解析逻辑产生，框架负责后续调度。复杂大规模采集可以讨论这种模型；简单页面不必引入完整爬虫框架。

## 原生设计思路

定义 Spider 子类、设置 start_urls、编写异步 parse，根据 Response 提取记录并产生 Request。跨请求状态、去重、会话、失败处理与输出需要有明确约定。同步 Fetcher、异步会话与 Spider 回调应按各自 API 使用，不能在运行中的事件循环里随意嵌套 asyncio.run。

## 与现有平台的对应关系

当前任务入口仍是一个普通 Python 文件。平台提供任务队列和版本，collection-v1 提供 get/render/crawl 等受控接口。没有注册一个接收任意 Spider 工程、配置或输出目录的通用运行入口。不能通过给草稿写 Spider 子类就宣称已接入原生调度器。

## 需求映射

字段解析 → Selector；公开 HTTP 批量采集 → crawl；页面交互 → 探索工具或已支持的动态会话；定时执行 → 平台任务计划；自动维护 → 平台版本修复。解释原生导出器或通用爬虫模板时附官方来源，实际运行路径仍需单独接入与验证。
