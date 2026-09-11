# DrissionPage：定位与选型
工具：drissionpage
接入状态：drissionpage-v1 原生 Windows 可选方案；不在 collection-v1 内执行
主题：定位、比较、安装、版本
版本：官方文档 4.1.1.4；不是本项目安装版本
核对日期：2026-09-11
适用范围：DrissionPage 原生用法与选型参考，不是平台可执行接口
来源：[官方首页](https://drissionpage.cn/)
来源：[安装](https://drissionpage.cn/get_start/installation/)

## 定位与平台状态
DrissionPage 是 Python 网页自动化库，不是编程语言。平台当前增加了 [drissionpage-v1 执行规则](runtime.md)，通过 save_draft → test_collection 串行试跑；部署需要固定依赖。不能因为检索到本文，就生成声称能在 collection-v1 中运行的 DrissionPage 脚本。

## 与现有工具如何搭配
原生 DrissionPage 兼有浏览器控制和请求模式。SpiderFly 目前仍通过 Scrapling、Playwright 及受控接口完成探索与采集。比较时按交互步骤、数据来源、运行环境选型；不能笼统说加入 DrissionPage 就更快或能绕过风控。
用户问理论用法，可以解释原生接口；用户指定 DP 创建任务时先读 runtime.md，再使用原生 DP 脚本和现有试跑工具。

## 完整参考资料入口
已吸收用户提供的 4.1.1.2 Markdown 资料：59 篇技术文档按 API 章节检索，另有 [完整目录](catalog.md) 和 [任务浏览器与端口](ports.md)。回答具体参数时优先补读对应章节，不能只凭本概览作答。
每条导入资料带原始相对路径和 SHA-256。4.1.1.2 与本概览先前参考的官网 4.1.1.4 要分别说明；官网链接是可变页面，不把两个版本混成已验证的一套接口。

## 版本、安装与来源
本次参考官网标注的 4.1.1.4 文档，不混用 5.0 测试版 API。官网页面是可变链接，核对日期不代表永久快照。浏览器控制需准备兼容 Chromium 浏览器；平台执行器要求 DrissionPage==4.1.1.4；实际部署需检查环境。
官网列有商业使用授权要求；引入运行依赖前应核对拟用版本的条款。这里是独立整理的索引和简要说明，不镜像或复制整套上游文档。
