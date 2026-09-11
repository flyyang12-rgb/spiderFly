# 公开网页采集：Scrapling 与 Playwright

版本：Scrapling 0.4.15；平台探索与 collection-v1 适配
适用范围：既有平台操作约定与带日期的历史观察
来源：[Scrapling 0.4.15](https://github.com/D4Vinci/Scrapling/tree/333fa22b7a5821194ce66b59b11f4b16a6484f02)

2026-09-11 新增分主题知识位于 scrapling/：overview、selectors、http、dynamic、sessions、pagination、concurrency、adaptive、spiders、proxies、troubleshooting、recipes、rag。解释原生接口时按主题检索，结合下列平台约定。文中的“已准备”与网站条数是当时记录，不能视为当前环境或网站的现场状态。

核对日期：2026-09-09。已准备 collection-v1，Python 3.12、Scrapling 0.4.15、Playwright 1.62.0 和 Chromium。现有 AI 创建入口可保存并实际试跑采集草稿。

## 默认先用原生 Scrapling（2026-09-10 实测修正）

需要连续操作或只能读取第一页时，用 scrape_live_open 建立 AsyncStealthySession 持久无头会话，scrape_live_act 按实际 CSS/文本选择器 hover/click/fill/select/press/scroll/wait；scrape_inspect 查看更新后的局部 DOM。浏览器网络证据包含真实 JSON 数组路径、字段名、记录数、hasMore 及公开搜索/分页参数，不返回凭据。不要把网页URL的page参数当作已验证的接口分页。

scrape_json_extract 可从 network.json.response_id 对应的真实响应提取：rows 是数组点路径，fields 是字段名到相对点路径，也支持 `{字段} {另一字段}` 文本组合和同站链接模板。filters 示例 `{"城市":{"equals":"杭州"},"岗位":{"contains_any":["RPA","影刀"],"excludes_any":["实习"]}}`，规则必须符合原需求；unique_by 和 limit 与 DOM 提取相同。只能提取标量，不能输出凭据/私人联系方式字段。响应缓存仅保留最近12项，过期需重新获取。

不直接因为登录弹窗结束：先区别推广层、反爬跳转、匿名展示和真实接口限制。原生会话可正常操作公开筛选，保持原城市和岗位条件，分地区等条件分批累计时先清除上次多选，按真实主键去重，不用无关职位凑数，也不把分批汇总说成网站默认排序前100。实际未达到数量如实报告。没有证据不能承诺登录后一定够数。

默认 scrape_search → scrape_page(mode="auto") → scrape_inspect → scrape_extract。auto 先带 Chrome 指纹的 Fetcher，静态空壳再用无头 StealthyFetcher；已经确认普通动态采集受检测时直接 mode="stealth"。原生引擎均在后台，不先弹出可见窗口或要求用户登录。HTTP 200 也可能只是加载中；必须检查真实文本与记录。页面有登录入口并不代表岗位都不可读。

scrape_page 的 wait_for 是实际观察到的 CSS，未知传空串。scrape_inspect 的 selector 定位快照内的列表区域，先从返回 links 的 class/parents 判断。scrape_extract 参数与 browser_extract 相同，使用上次原生采集快照，字段/主键一致时跨页累计。选择器来自实际页面，不套用别的网站。

本机原生实测：BOSS 杭州 RPA 搜索，Fetcher 返回加载中，DynamicFetcher 导向登录页，StealthyFetcher 在无账号会话下读取到 15 个不同的 RPA 相关职位。该记录不保证以后始终有效，不证明已有100条；不得套用“BOSS一定要登录”的结论。只有原生采集仍不足且实际需要用户交互时再使用下面的浏览器工具。

## 浏览器自主探索（2026-09-10）

现有 Agent 已有 browser_search/open/observe/act/network/extract/wait_user/close 工具。用户指定网站名称即先搜索核实入口，不能只凭网站通常有风控就拒绝。每次操作使用最近观察的元素 ref；页面变化后重新观察。structure 是删去脚本和输入值的页面结构，可据此生成 CSS；不要猜测不存在的字段。

探索使用 Windows 独立 Chromium 会话，正常请求可运行页面自身的搜索、筛选和动态加载。用户在宿主机窗口完成登录或验证，再点击“我已处理，继续”；模型不接触密码，不发送消息、投递简历或修改账号。用户明确的采集需求允许探索相关公开站点和页面所需资源，浏览器实际发现的域名可以交给静态读取与 collection-v1 试跑，平台仍逐次检查公网地址。

browser_extract 用 Scrapling 解析当前 DOM：rows 传记录 CSS；fields 传 JSON，例如 {"岗位":".job-name::text","链接":"a::attr(href)"}（仅示例，实际选择器必须来自页面）。unique_by 传 ["链接"] 这样的 JSON 字符串，limit 传用户目标数量的字符串。先提取少量样本核对城市、岗位相关性等条件，再翻页累计；字段和去重主键应保持一致。平台按主键去重、限制累计条数，返回真实样本、总数和 CSV 下载地址。不能把字段数量检查当成业务语义已验证。

会话跨对话轮次保留，闲置 30 分钟、手工关闭或服务重启后结束，不读取个人浏览器，也不保存 Cookie 到脚本。页面提取结果与最后观察持久保存。遇到明确限制暂停或降频，不无限重试。浏览器预算仍受每轮模型次数、时长和 Token 限额控制。

下面的 collection-v1 限制仅适用于生成脚本的 WSL 试跑和定时运行，不等于探索浏览器不能登录。浏览器登录状态目前不会自动转入 WSL。依赖登录的采集可先用 browser_extract 交付文件；公开采集可生成 Python、save_draft、test_collection。不得声称登录采集脚本已经能无人值守运行。

## collection-v1 脚本约定

- 入口仍是单文件 Python，前十行内写 `# spiderfly-runtime: collection-v1`。不能生成 Node.js 主程序，不需要 JS 任务入口。
- 使用内置 `spiderfly_collection` 的 `get`、`render`、`browser`。这是环境提供的采集接口，不是需要 pip 安装的包；不要导入平台 app 或本机 flows。get 使用受控 Scrapling FetcherSession；render 使用原生 DynamicSession，browser 保留 Playwright 操作接口。
- 静态页面先用 get，CSS/XPath 依据真实页面。需要点击、滚动、动态内容时使用 browser；必要的 JS 用 page.evaluate。collection-v1 不运行 DP；用户选择原生 DP 时使用 drissionpage-v1，先读 drissionpage/runtime.md，再调用 dp_probe、save_draft、test_collection。
- 在本轮用户需求允许的站点内采集，可跟随同站翻页链接；必须保留城市、条件、字段及数量要求，不能凑数。外部 API/CDN 域名可以来自用户提供或本对话浏览器实际发现；没有探测证据的域名仍需先探索。
- 仅公开 GET，可使用本次运行的匿名站点 Cookie 会话；无个人浏览器、Cookie、登录、POST 或 WebSocket。登录、验证码、401/403/429 时停止并报告原因，不反复尝试。
- 每次试跑最多 120 秒（含排队），300 个请求，单响应 2MB，总响应编码 32MB；结果最多 20 个、合计 8MB。时间/量不足就说明实际完成量，不承诺任意站点任意数量。
- 结果写入 SPIDERFLY_ARTIFACT_DIR；保存真实来源 URL、采集时间并去重。薪资保留原文，按用户指定口径排序。
- 原需求足够明确时写 SPIDERFLY_ACCEPTANCE，effects=artifacts_only，填写 file/format/required/min_rows/max_rows/unique_by/urls。urls 列明起始页面及必要的已授权站点。独立检查只证明声明的字段、数量等条件，不等于能自动判断所有内容真实性。
- save_draft 后调用 test_collection，draft_id 传字符串。先看实际日志、行数和文件；失败调整代码再保存新版本再试跑，每轮最多三次。不修改原需求来通过测试。通过后告知使用草稿创建任务即可，定时执行使用同一 collection-v1 环境并重新采集。

## 静态采集例子

以下两条记录只是接口示例，按用户实际数量修改，不要照抄当作需求。

```python
# spiderfly-runtime: collection-v1
import csv, os
from pathlib import Path
from spiderfly_collection import get
SPIDERFLY_ACCEPTANCE = {
    'effects': 'artifacts_only', 'file': 'books.csv', 'format': 'csv',
    'required': ['title'], 'min_rows': 2, 'max_rows': 2,
    'unique_by': ['title'], 'urls': ['https://books.toscrape.com/']
}
page = get('https://books.toscrape.com/')
titles = page.css('h3 a::attr(title)').getall()[:2]
with (Path(os.environ['SPIDERFLY_ARTIFACT_DIR']) / 'books.csv').open('w', newline='', encoding='utf-8-sig') as stream:
    writer = csv.writer(stream)
    writer.writerow(['title'])
    writer.writerows([[title] for title in titles])
print(f'实际采集 {len(titles)} 条')
```

依赖可声明 `scrapling==0.4.15`。翻页使用 urllib.parse.urljoin 拼接页面上的 next 链接，再调用 get；不要预先猜测详情 URL。

## 动态页面

```python
from spiderfly_collection import browser
with browser() as page:
    page.goto(URL, wait_until='domcontentloaded')
    page.locator(实际选择器).first.wait_for()
    # 可用 locator.click、inner_text、evaluate 等 Python Playwright API。
    # 在 with 内读取结果，退出时关闭本次浏览器。
```

需要 Scrapling 解析动态 HTML 时使用 `render(URL, wait_for=实际选择器)`，返回同样支持 css/xpath 的 Selector。

来源：https://github.com/D4Vinci/Scrapling 和 https://playwright.dev/python/docs/intro 。接入的是页面解析和受控浏览器采集，不代表稳定绕过网站风控。

## 把对话要求写进脚本

- 明确的并发、字段、筛选、排序及续采要求必须写成源码中的实际调用和参数；保存并试跑后再说明结果，不只在回复中承诺。用户要求 4 并发的 HTTP 批量采集使用 `crawl(..., concurrency=4)`；超过 4 时说明上限，不静默降级。
- 用户不指定时按页面选择方式；crawl 省略 concurrency 时默认 2，不代表所有采集自动并发。需要逐页发现链接、只有一个待采网址、同一个命名会话或浏览器连续操作时，说明串行约束，不承诺 4 倍速度。
- 并发只发生在单个 HTTP 采集任务内部，平台仍串行执行任务，不把此参数解释为多任务或多浏览器并行。
- 正常定时运行沿用保存的脚本与参数，不重新对话或调用模型。后续需求变化先保存并验证新草稿，不声称已自动修改既有任务和计划。

## 原生采集与断点

- `fetch(url, wait_for=".item", mode="auto")`：先 HTTP；仅缺少指定内容时切到 DynamicSession，日志说明原因。已知动态页面使用 mode="dynamic"，不把限流或登录问题当作切换理由。
- `with session("catalog") as s: s.get(url)`：跨页复用本次任务的匿名 HTTP Cookie。不同名称隔离，最多 8 个，不读取个人 Cookie，不跨执行保留登录。
- `with dynamic_session() as s: s.fetch(url, wait_for=".item", page_action=操作函数)`：原生 Scrapling DynamicSession。操作函数接收 Playwright Page；动作失败必须报告失败。
- 批量 HTTP 采集优先 `crawl(start_urls, parse, concurrency=2, max_pages=300)`。parse(page) 返回 `(记录字典列表, 后续链接列表)`；可以返回相对链接。并发 1–4，总请求上限仍为 300，全局请求起始间隔至少 0.1 秒；同一个命名 HTTP 会话串行保证 Cookie 顺序。
- crawl 每解析完成一页保存待采网址和记录。失败后，下次运行同任务同源码及同验收可继续；维护试跑使用临时断点，不污染正式进度。新源码或验收改变后重新开始。成功且结果验收通过才清除断点，下一次定时重新采集。不会自动增加运行或无限续跑。
- 最终仍由脚本把 crawl 返回的记录写为约定 CSV/JSON/XLSX；不要只保存断点就报告成功。断点每任务/草稿最多一版、8MB，不含 Cookie。断点功能来自平台适配层，未开放 Scrapling Spider 的任意文件目录或全接口。

持久结果筛选：发现候选混入无关记录时，使用 browser_refine_results(action=filter,filters=完整字段规则)。同对话后续 DOM/JSON 提取自动沿用规则，不会把排除项重新加回。可用 action=undo 撤销最近一次筛选，恢复筛选前记录及规则。原字段集合相同但顺序不同允许继续，CSV维持首次列顺序。空结果页的推荐卡片不代表满足搜索条件，应优先读取真实搜索响应。
