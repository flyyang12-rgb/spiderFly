# 公开网页采集：Scrapling 与 Playwright

核对日期：2026-09-09。已准备 collection-v1，Python 3.12、Scrapling 0.4.15、Playwright 1.62.0 和 Chromium。现有 AI 创建入口可保存并实际试跑采集草稿。

## 必须遵守

- 入口仍是单文件 Python，前十行内写 `# spiderfly-runtime: collection-v1`。不能生成 Node.js 主程序，不需要 JS 任务入口。
- 使用内置 `spiderfly_collection` 的 `get`、`render`、`browser`。这是环境提供的采集接口，不是需要 pip 安装的包；不要导入平台 app 或本机 flows。get 使用受控 Scrapling FetcherSession；render 使用原生 DynamicSession，browser 保留 Playwright 操作接口。
- 静态页面先用 get，CSS/XPath 依据真实页面。需要点击、滚动、动态内容时使用 browser；必要的 JS 用 page.evaluate。DP 未接入，不生成 DP 程序。
- 在本轮用户需求允许的站点内采集，可跟随同站翻页链接；必须保留城市、条件、字段及数量要求，不能凑数。外部 API/CDN 域名需要用户提供，遇被拦截域名先说明缺少哪个域名，不能自行授权。
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
