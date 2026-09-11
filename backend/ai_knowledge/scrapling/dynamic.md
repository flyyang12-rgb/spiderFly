# 动态网页、无头浏览器与等待
工具：scrapling
接入状态：已有受控适配；具体原生功能是否开放以正文和平台接口为准
主题：dynamic

版本：Scrapling 0.4.15；项目锁定版本
核对日期：2026-09-11
适用范围：原生 Dynamic/Stealthy 参考；探索与脚本能力不同
来源：[docs/fetching/dynamic.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/fetching/dynamic.md)
来源：[docs/fetching/stealthy.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/fetching/stealthy.md)

关键词：DynamicFetcher StealthyFetcher JavaScript 渲染 无头 页面等待 wait_for wait_selector page_action。

## 什么时候用浏览器

HTML 没有目标字段、需要 JavaScript 请求数据、需要点击或滚动触发内容时，使用浏览器。先看 HTTP 正文和网络响应，不把“静态选择器没匹配”一律当动态问题。DynamicFetcher 和 StealthyFetcher 都是浏览器抓取；stealth 不表示不用浏览器，也不保证某网站必定可访问。

## 正确等待目标内容

等真实列表或加载完成标记，比固定 sleep 更能判断页面是否就绪。持续轮询或长连接页面不一定达到 network_idle；不要把等待超时直接理解成没有结果。记录等待的选择器、当前 URL、响应和样本。先观察后选选择器，不能套用别的网站 class。

## 原生与平台参数分开

原生浏览器文档有 wait_selector、timeout、page_action 等参数，浏览器 timeout 通常按毫秒。SpiderFly 的 scrape_page 使用 wait_for 字符串；collection-v1 的 render(url, wait_for=...) 和 dynamic_session().fetch(url, wait_for=..., page_action=...) 是平台包装接口，不接受原生类全部配置。

探索的持久无头会话用 scrape_live_open/act；动态快照用 scrape_page(mode='dynamic'/'stealth')。collection-v1 使用 DynamicSession 的受控实现，没有提供 StealthySession 或任意 CDP 地址的脚本入口。

## 连续操作流程

观察 DOM 与网络 → 找唯一可操作元素 → 点击/填写/滚动 → 等待目标变化 → 重新观察 → 提取和检查。page_action 在原生或受控会话中接收 Playwright Page；动作报错不能吞掉。browser() 的资源在 with 范围内使用并关闭。探索登录态不能直接继承到定时脚本。
