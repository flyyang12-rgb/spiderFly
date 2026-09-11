# 并发、限速与断点续采
工具：scrapling
接入状态：已有受控适配；具体原生功能是否开放以正文和平台接口为准
主题：concurrency

版本：Scrapling 0.4.15；项目锁定版本
核对日期：2026-09-11
适用范围：SpiderFly crawl 适配层，非完整原生 Spider 配置
来源：[docs/spiders/advanced.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/spiders/advanced.md)
来源：[docs/spiders/architecture.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/spiders/architecture.md)

关键词：并发 concurrency 异步 async crawl 断点 checkpoint resume 续采 恢复 限速。

## 单任务并发与平台串行

平台一次运行一个任务。单个采集脚本可用 crawl(urls, parse, concurrency=2, max_pages=300, session_name='') 重叠 HTTP 请求；concurrency 为 1–4，默认 2。处理页面与提交断点按确定顺序串行。同一个命名 HTTP 会话仍串行。

请求存在先后依赖、只有一个已发现链接、浏览器连续操作时，并发可能无收益。用户指定并发要落实到参数；超过上限明确说明。不能把并发 4 等同四个任务同时运行或四倍速度。

## crawl 的数据协议

parse(page) 返回 (记录字典列表, 后续链接列表)。后续链接可为相对地址。urls、max_pages、session_name 属于断点身份；原任务、源码与验收条件也参与平台隔离。每解析完一页保存记录和待采列表，最后由脚本写入产物文件。

## 断点什么时候有效

失败后的同任务同版本、相同条件可在后续运行恢复；维护验证使用临时断点，不污染正式进度。源码或验收改变要重新开始；成功且结果验收通过后清理，下一次计划重新采集。断点不含 Cookie，不会自动增加无限运行次数。

## 请求与产物上限

现有采集环境限制包含单次运行时长、300 个请求、单响应 2MB、总响应量和产物大小。crawl max_pages 不是额外请求额度：网页资源请求也会占预算。运行超时继续受平台配置约束。触达限制时保存实际进度并报告，不能降低用户目标后声称已完成。

## 原生机制区别

原生 Spider 的并发、存储目录和恢复机制有自己的配置。本项目 crawl 是受管适配层，不能把原生 Spider 的全部设置直接传给 crawl。若讨论大规模爬虫架构，可以解释原生方案，并明确当前平台未部署对应完整引擎。
