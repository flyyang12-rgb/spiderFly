# 会话、Cookie 与登录状态
工具：scrapling
接入状态：已有受控适配；具体原生功能是否开放以正文和平台接口为准
主题：sessions

版本：Scrapling 0.4.15；项目锁定版本
核对日期：2026-09-11
适用范围：原生会话参考；平台按对话或执行隔离
来源：[docs/fetching/static.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/fetching/static.md)
来源：[docs/spiders/sessions.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/spiders/sessions.md)

关键词：Cookie session 登录态 持久会话 复用 FetcherSession DynamicSession AsyncStealthySession。

## 为什么需要会话

同一任务多次请求可能需要保留匿名 Cookie 或浏览器状态。原生 FetcherSession 复用 HTTP 会话；DynamicSession 和 StealthySession 复用浏览器资源；异步会话提供相应异步调用方式。会话的关闭与生命周期由调用方管理，不能把创建一次会话等同于永久登录。

## 平台三种状态

| 场景 | 保存什么 | 能否传给定时任务 |
| --- | --- | --- |
| scrape_live_open/act | 当前对话的无头页面和会话 | 不能自动传递 |
| 可见 browser_open | 独立浏览器；用户可在窗口处理登录 | 不能自动传递 |
| collection-v1 session(name) | 本次执行的匿名 HTTP Cookie | 不跨执行保存登录 |

受限脚本的命名 HTTP 会话最多 8 个，同名会话请求串行以维持 Cookie 顺序。多个匿名会话不是多账号登录，断点也不保存 Cookie。服务重启、超时或会话关闭后应重新检查，不能声称旧页面仍存在。

## 登录提示怎么判断

先区分普通登录入口、遮罩、跳转与真实接口拒绝。基于当前响应说明已观察到的情况；不能仅凭经验说“这个站一定必须登录”，也不能承诺登录后一定可获得指定条数。需要用户交互时使用现有等待用户流程。知识库可解释认证原理，不提供读取个人浏览器凭据的入口。
