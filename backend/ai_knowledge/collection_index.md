# 采集知识库：定位与索引
工具：platform
接入状态：参考索引，不授予执行能力
主题：知识库定位、工具选型、目录
版本：SpiderFly 工作区 2026-09-11
核对日期：2026-09-11
适用范围：AI 技术问答与文档导航

## 知识库定位
本库帮助 AI 解释用法、比较工具、查找接口与限制。它不是执行引擎，不是用户网站业务数据库，也不是当前网页证据。文档不能新增工具或安装依赖。回答以接入状态为边界；未标注状态不能推断为已支持。实际采集仍使用现有探索、保存草稿和试跑流程。

## 工具索引
| 工具 | 入口 | 平台状态 |
| --- | --- | --- |
| Scrapling | [概览](scrapling/overview.md) | 已有受控适配，非全部原生能力 |
| DrissionPage / DP | [概览](drissionpage/overview.md)、[59 篇本地技术文档](drissionpage/catalog.md) | [drissionpage-v1 原生可选方案](drissionpage/runtime.md)，需固定依赖 |
| Playwright | [动态页面](scrapling/dynamic.md) | 已有浏览器相关能力，以实际工具为准；暂无独立完整专题 |

## 浏览器连接问题
DP 创建与试跑先读 [当前平台执行规则](drissionpage/runtime.md)。默认端口、接管任务浏览器、保留登录、与 Playwright 对接再读 [任务浏览器与端口](drissionpage/ports.md)，再按需查 [原生连接文档](drissionpage/reference/browser_control/connect_browser.md)。不要默认把任务浏览器和 DP 的默认 9222 当成同一个浏览器。

## 按问题查章节
| 问题 | Scrapling | DrissionPage |
| --- | --- | --- |
| 页面获取与解析 | [HTTP](scrapling/http.md)、[选择器](scrapling/selectors.md) | [浏览器与定位](drissionpage/browser.md) |
| 会话和登录态 | [会话](scrapling/sessions.md) | [请求与会话](drissionpage/sessions.md) |
| 翻页与接口 | [翻页](scrapling/pagination.md) | [网络监听](drissionpage/network.md) |
| 并发和断点 | [并发与恢复](scrapling/concurrency.md) | 暂未整理专题，不推断为库不支持 |
| 原生扩展 | [自适应](scrapling/adaptive.md)、[Spider](scrapling/spiders.md)、[代理](scrapling/proxies.md) | 查官方对应版本，不能套用另一库的 API |
| 报错和编写 | [排错](scrapling/troubleshooting.md)、[配方](scrapling/recipes.md)、[RAG](scrapling/rag.md) | 从上述专题查起 |

## 索引使用
先按工具名和主题检索，再用返回的 name、section 补读。版本、来源、接入状态随章节传递；没找到资料表示未收录或未命中，不表示该库没有此功能。比较工具时分别引用双方来源，不能混写 API。
