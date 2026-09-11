# DrissionPage：任务浏览器、端口与接管
工具：drissionpage
接入状态：原生 API 参考；当前平台接入范围以 runtime.md 为准
主题：端口、接管、同一个浏览器、existing_only、用户目录、启动、退出
版本：用户提供的 Markdown 文档 4.1.1.2；项目使用建议单独标注
核对日期：2026-09-11
适用范围：原生连接规则；当前执行约定见 runtime.md
来源：[官方连接说明（可变网页）](https://www.drissionpage.cn/browser_control/connect_browser/)
来源：[官方配置说明（可变网页）](https://www.drissionpage.cn/browser_control/browser_options/)

## 任务与 DP 操作同一个浏览器
项目使用建议：如果要让 DP 操作任务已经打开的浏览器，先获取该浏览器实际的调试地址，再让 DP 连接这个地址。不能因为默认是 9222 就猜任务也使用 9222。端口是浏览器调试端口，不是 SpiderFly 网页服务端口。
同一个浏览器不等于同一个标签页：连接后还要根据任务记录的标签页 ID 等信息选择目标，不能默认 latest_tab 就是任务页面。
当前 drissionpage-v1 已通过 SPIDERFLY_BROWSER_ADDRESS 传入本次地址，读取普通任务端口配置，默认 9123；平台专门创建单个任务页面，脚本使用 latest_tab。详见 [当前执行规则](runtime.md)。

## 默认 9222、显式地址和只接管
本地参考文档说明：Chromium() 会读取 ini 或内置配置；默认配置使用 9222，但已修改 ini 时不一定是 9222。Chromium(9333) 会连接该端口已有浏览器，端口空闲时也可能启动新的。
如果需求是“只接管任务浏览器，不能另开”，可参考 ChromiumOptions().set_address(actual_address).existing_only()；连接失败应报告错误，不能静默转去默认端口或开另一个浏览器。actual_address 必须由任务配置或实际运行信息提供，不能编造。
原生配置详细参数见 [启动设置](reference/browser_control/browser_options.md)，连接行为见 [连接浏览器](reference/browser_control/connect_browser.md)。

## 新开独立浏览器与保留登录态
要新开独立浏览器时，auto_port() 使用空闲端口和临时用户目录，不适合作为“复用上次登录”的默认方案。手动多开需要各自独立的端口和用户目录。
复用登录态需要使用对应的浏览器或用户资料，端口相同本身不证明账户、登录有效期或任务归属相同。不要关闭或重建用户已有浏览器来迁就默认配置。new_env() 可能关闭指定端口的浏览器并重新创建，不能当作无副作用的连接操作。
原生说明见 [全新浏览器](reference/tutorials/functions/new_browser.md) 和 [浏览器多开](reference/tutorials/functions/create_browsers.md)。

## Playwright 或 Selenium 已经打开的浏览器
本地 4.1.1.2 资料还提供 from_playwright()、from_selenium() 对接方法，将对应对象转换为 ChromiumPage；只适用其支持的 Chromium 场景。不能把所有接管都解释为必须先人工固定端口。
这些是原生库参考，未在本平台验证或接入。选用前核对运行版本、对象类型和浏览器归属。见 [与其他项目对接](reference/advance/docking.md)。

## 浏览器什么时候关闭
任务启动首先是运行脚本，HTTP 请求或文件处理不一定需要浏览器。原生资料提示 Python 程序结束不一定关闭浏览器，尤其无头窗口不可见时仍可能有进程。
项目使用建议：区分本次创建与借用的浏览器；只关闭本次拥有的资源，接管的浏览器不默认 quit()。这不改变平台现有的任务清理实现。

## 版本疑点
这套本地文档在 auto_port 多进程描述上存在差异：连接页提示小概率冲突，启动配置页写不支持多进程。不能合并成“多进程一定安全”，需要按拟用版本源码和测试确认。任务队列仍保持项目现有的单宿主机串行约束。
