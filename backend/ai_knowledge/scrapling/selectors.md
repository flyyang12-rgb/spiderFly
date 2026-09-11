# 选择器、字段提取与数据清洗
工具：scrapling
接入状态：已有受控适配；具体原生功能是否开放以正文和平台接口为准
主题：selectors

版本：Scrapling 0.4.15；项目锁定版本
核对日期：2026-09-11
适用范围：Scrapling 0.4.15 原生解析；平台 Selector 可用
来源：[docs/parsing/selection.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/parsing/selection.md)
来源：[docs/parsing/main_classes.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/parsing/main_classes.md)

关键词：CSS XPath Selector 字段提取 缺失值 文本 属性 相对路径 去重。

## CSS 与 XPath 怎么选

先定位每条记录的容器，再在容器内提取字段，避免把不同记录的标题和价格拼在一起。CSS 适合 class、标签、属性；XPath 适合父子关系和更复杂条件。XPath 在记录内通常使用 .//，避免 // 回到整篇文档。get() 取单值，getall() 取多个值；缺失字段先保留为空并按用户规则处理。

## 可离线运行的解析示例

这个 HTML 是合成数据，用于理解接口，不代表任何真实网站结构。

```python
from scrapling import Selector
from urllib.parse import urljoin
html = '<article class="item"><a href="/p/1">示例产品</a><span class="price">12.00</span></article>'
page = Selector(content=html, url='https://example.com/catalog')
rows = []
for item in page.css('article.item'):
    rows.append({
        'title': item.css('a::text').get() or '',
        'price': item.css('.price::text').get() or '',
        'url': urljoin('https://example.com/catalog', item.css('a::attr(href)').get() or ''),
    })
assert rows[0]['title'] == '示例产品'
```

## 字段正确性检查

文本可能有多层标签，直接 ::text 只覆盖相应文本节点；先检查样本再决定是否使用更完整文本提取。相对链接用实际响应 URL 做 urljoin。价格、日期、薪资等保留原始值，转换口径由需求决定。去重优先真实 ID 或规范化详情 URL，不能只按可能同名的标题去重。提取数量不等于业务条件已满足，仍需检查地区、类别与推荐内容混入。

## AI 工具与原生参数区别

scrape_extract/browser_extract 的 fields 是“字段名 → CSS”的 JSON 字符串；scrape_json_extract 用 JSON 点路径，不是 CSS/XPath。已有工具的 fields/rows/unique_by/limit 参数不能换成原生 Selector 的任意方法名。需要原生 XPath 复杂解析时，生成受限脚本并试跑。
