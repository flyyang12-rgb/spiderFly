# 受控采集脚本配方与离线例子
工具：scrapling
接入状态：已有受控适配；具体原生功能是否开放以正文和平台接口为准
主题：recipes

版本：Scrapling 0.4.15；项目锁定版本
核对日期：2026-09-11
适用范围：collection-v1 接口示例；真实网站必须重新观察和试跑
来源：[docs/parsing/selection.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/parsing/selection.md)
来源：[docs/fetching/choosing.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/fetching/choosing.md)

关键词：示例 配方 模板 Python CSV crawl parse get render 验收。

## 配方一：单页采集的骨架

页面地址与选择器是示例占位，不是已验证的业务任务。真实任务应替换成观察到的值，并按需求声明 SPIDERFLY_ACCEPTANCE。

```python
# spiderfly-runtime: collection-v1
import csv
import os
from pathlib import Path
from spiderfly_collection import get
URL = 'https://example.com/catalog'
ROW_CSS = '.item'
page = get(URL)
rows = [{'title': item.css('.title::text').get() or ''} for item in page.css(ROW_CSS)]
if not rows:
    raise ValueError('未匹配记录，请核对真实页面与选择器')
output = Path(os.environ['SPIDERFLY_ARTIFACT_DIR']) / 'items.csv'
with output.open('w', newline='', encoding='utf-8-sig') as stream:
    writer = csv.DictWriter(stream, fieldnames=['title'])
    writer.writeheader()
    writer.writerows(rows)
print(f'实际保存 {len(rows)} 条')
```

## 配方二：翻页与并发

parse 回调必须返回记录和后续 URL。下面只展示接口组织，不能照搬选择器或把输出条数当已满足用户需求。

```python
from spiderfly_collection import crawl

def parse_page(page):
    rows = [{'title': item.css('.title::text').get() or ''} for item in page.css('.item')]
    next_urls = page.css('a.next::attr(href)').getall()
    return rows, next_urls

rows = crawl(['https://example.com/catalog'], parse_page, concurrency=2, max_pages=20)
# 接着按配方一保存 rows；仅得到 rows 或断点不算交付文件。
```

## 配方三：需要交互的动态页面

用 from spiderfly_collection import dynamic_session，在 with dynamic_session() as current 中调用 current.fetch(URL, wait_for=实际CSS, page_action=操作函数)。操作函数接收 page，使用观察到的定位器点击或滚动；退出 with 关闭资源。此处 wait_for 是平台参数，不要机械复制原生 wait_selector。

## 提交顺序

读取当前任务/草稿 → 检索相关知识 → 根据真实页面写脚本 → save_draft → test_collection → 核对产物与验收 → 创建任务或提交版本候选。不要 pip install spiderfly_collection，它是环境内置接口。运行脚本不得导入平台 app、访问平台数据库或继承探索会话 Cookie。
