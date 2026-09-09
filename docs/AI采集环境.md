# AI 采集环境

在原“AI 创建”或任务“AI 助手”中给出网址、条件、数量和输出字段。Agent 生成 Python 草稿后调用采集试跑工具；页面显示等待、正在采集、通过或失败，并提供实际结果文件和日志。每轮最多试跑三次。

试跑通过后点击“使用草稿”，按原流程创建任务和设置定时。开头包含 `# spiderfly-runtime: collection-v1` 的任务，在入队时绑定采集环境；首次运行、定时运行及修复后的重跑均使用它，重新访问网站并执行原验收。试跑不会自动创建或修改定时计划。

## 用对话设置采集方式

直接告诉 AI 网址、字段、数量、筛选和排序，也可以指定并发和恢复要求。例如：

> 采集我提供的网站，用 4 并发抓详情，保存来源链接，导出 Excel；失败后下次继续。

AI 应将要求写入一个 Python 草稿，使用 `crawl(..., concurrency=4)` 等实际接口，再试跑验证。说“已使用并发”不代表已经实现，应核对源码配置和试跑结果。依赖仍单独保存在 requirements.txt，公共采集能力由平台环境提供。

| 对话要求 | 脚本与运行规则 |
| --- | --- |
| “用 4 并发” | HTTP 批量采集设置 concurrency=4；目前支持 1–4，仍受限速和时间预算约束 |
| “失败后下次继续” | 使用 crawl 按页保存断点；同任务、同代码和同验收下恢复 |
| “没有指定方式” | AI 根据页面选择 HTTP 或动态采集；使用 crawl 且省略并发参数时默认 2 |
| “每天执行” | 草稿通过后在原任务表单设置定时；之后执行保存的脚本，不必重复对话 |

并发是单个任务内同时请求多个页面，不是多个任务同时运行，也不是启动 4 个浏览器。只有一个待采页面、后续链接必须逐页发现，或使用同一个命名会话时，实际请求可能串行；不能承诺 4 倍速度。超过当前范围的要求，AI 应说明限制。

之后对话提出修改会生成新草稿，不会直接改变已经保存的任务或定时计划。需要按现有流程使用并验证新草稿。

## 实际使用的工具

| 场景 | Python 接口 |
| --- | --- |
| 静态网页、同站翻页 | `spiderfly_collection.get(url)`：原生 Scrapling FetcherSession 请求，Selector 解析 |
| 动态 HTML | `spiderfly_collection.render(url, wait_for=selector)`：原生 DynamicSession |
| 点击、滚动、页面内 JS | `with spiderfly_collection.browser() as page`：Playwright Python |
| 结果处理与导出 | Python 标准库、openpyxl、BeautifulSoup、requests |

Scrapling 版本为 0.4.15，Playwright 为 1.62.0。页面内 JavaScript 通过 Python 的 `page.evaluate` 执行，不需要增加 JS 任务入口。当前接入原生 FetcherSession 和 DynamicSession；并发和断点通过平台 crawl 适配层提供，不开放任意 Spider 文件目录。没有接入 DP 或 StealthyFetcher，也不保证自动绕过风控。

`spiderfly_collection` 是平台采集环境内置模块，不是独立 pip 包。草稿依赖可以声明 `scrapling==0.4.15`、`playwright==1.62.0` 等已支持版本。原 Windows 任务的依赖准备流程仍保留，但实际采集及浏览器使用共享的 WSL 环境，不为每个草稿版本复制 Chromium。

## 会话、并发和恢复

- `fetch(url, wait_for=".item", mode="auto")` 先用 HTTP；找不到指定内容时切为动态采集并记录原因。访问拒绝不自动换框架重试。
- `with session("catalog") as s` 后调用 `s.get(url)`，保持本次运行的匿名站点 Cookie。最多 8 个命名会话，会话之间隔离，运行结束销毁；不复用个人登录。
- `with dynamic_session() as s` 后调用 `s.fetch(url, wait_for=".item", page_action=函数)`。动作函数接收 Playwright Page，异常会使采集失败。
- `crawl(urls, parse, concurrency=2, max_pages=300)` 提供 1–4 并发取页；parse 返回记录列表和下一批链接。同一命名 HTTP 会话内按串行请求保持 Cookie 顺序，任务之间仍由平台串行调度。
- 每页解析成功后原子保存记录和待采链接。失败后再次运行同任务、同源码和同验收时继续；代码、依赖、站点或验收变更后重新开始。维护验证用临时断点，不修改正式任务断点。
- 原结果验收通过才清除断点，下一次定时从头采集新数据。恢复不额外创建执行、不无限重跑，也不延长 120 秒预算。断点保存在本机 `data/collection_state/`，按任务/草稿隔离，每个范围只留一版，最大 8MB，不保存 Cookie；随 data 一起备份。任务不再使用时可能留下未完成断点，当前没有后台过期清理。
- 最终结果仍须写入原约定文件。断点保留已处理页面，不是数据库事务，失败页可能再次请求；动态网站数据也可能在两次运行间变化。

## 范围和限制

- 只读取用户明确给出且写入原验收 `urls` 的公开站点，允许站内翻页和详情请求。第三方 API、CDN 域名也需要用户提供并列入验收；不会自动扩大站点权限。
- 请求经过独立的 GET 通道：逐次检查 DNS、公网 IP 及重定向，并连接检查后的地址。不携带个人 Cookie、登录凭据、用户请求头或请求体，不支持 POST、WebSocket、登录和验证码处理。
- Chromium 在无直接外网的 Linux 隔离环境中运行，浏览器请求通过同一通道获取。没有宿主机用户目录或个人浏览器资料；不接管 Windows Excel 或已有浏览器。
- 每次试跑最多 120 秒，包含排队等待；正式采集最多运行 120 秒。每轮 AI 最多三次试跑，仍受原模型时间及调用预算限制。每次执行最多 300 次取页，每次最多跟随 5 次重定向，请求间至少间隔 0.1 秒，单响应 2MB，总响应 Base64 编码计数不超过 32MB。
- 产物最多 20 个、合计 8MB；工作区为临时文件系统，浏览器临时目录单独提供 128MB。浏览器允许子进程，单进程数据段限制 2GB、用户进程/线程数上限 128；这不是整个进程树的统一内存配额。旧 readonly-v1 的禁止子进程及 512MB 限制不变。
- 登录、403、429 或验证码需要用户处理；不能承诺稳定绕过风控。内容变化、请求上限和网站速度都可能导致无法达到目标条数，日志保留实际结果。

## 结果检查

脚本必须声明 `SPIDERFLY_ACCEPTANCE`，包括只生成产物、结果文件、格式、字段、数量以及起始网址。平台在脚本之外检查文件、行数、空值、唯一性、固定值和正数，并记录实际网络请求。续采可以使用此前真实请求产生的断点；只写固定样例、且没有真实请求或有效续采记录不会通过采集试跑。

这些检查只验证声明条件，不证明每条数据的全部业务含义。需求如城市、薪资口径、去重及来源仍应在脚本中正确实现。失败时保留已经生成的文件和错误，修改代码后产生新草稿；旧草稿的验证结果不会转移到新源码。

试跑与业务运行共用串行队列。关闭网页后继续，停止会通知执行环境结束，服务重启将未结束试跑标记中断而不自动重发。结果存入本机数据库并与草稿关联，仅该对话所有者和超级管理员可下载；删除所属对话/任务时随数据库关联清理。

采集任务的自动维护复用原预算、版本、通知及一次重跑机制。修复试跑在 collection-v1 重新访问网站，按冻结的原验收检查；这是实时采集验证，不是原网页内容的冻结重放。401/403/429 属于访问问题，不触发代码修复。

## 在新宿主机准备

先按 [AI 任务助手](AI任务助手.md#受限运行环境的准备与范围) 安装原 readonly-v1 基础工具，然后进入服务账号的 WSL Ubuntu：

```bash
python3 /mnt/d/你的项目路径/SpiderFly/backend/sandbox/setup_collection.py
sudo env PYTHONPATH="$HOME/.local/share/spiderfly-collection-v2/python" python3 -m playwright install-deps chromium
```

第一步安装到当前 Linux 用户的 `~/.local/share/spiderfly-collection-v2`，第二步准备 Chromium 需要的 Ubuntu 系统库。正式服务必须使用同一 WSL 用户。首次下载需要网络；完成后应做一次网页采集和浏览器试跑，单纯安装成功不等于浏览器可运行。环境未就绪时报告错误，不退回 Windows 直接执行生成代码。

来源：[Scrapling](https://github.com/D4Vinci/Scrapling)、[Playwright Python](https://playwright.dev/python/docs/intro)。
