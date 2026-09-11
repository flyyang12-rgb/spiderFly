# 翻页、JSON 接口与结果去重
工具：scrapling
接入状态：已有受控适配；具体原生功能是否开放以正文和平台接口为准
主题：pagination

版本：Scrapling 0.4.15；项目锁定版本
核对日期：2026-09-11
适用范围：平台探索工具 + 受控脚本流程
来源：[docs/parsing/selection.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/parsing/selection.md)
来源：[docs/spiders/requests-responses.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/spiders/requests-responses.md)

关键词：翻页 分页 pagination page offset cursor hasMore JSON 下一页 无限滚动 去重 重复数据。

## 先识别分页方式

常见方式有下一页链接、页码、offset/limit、游标和滚动加载。参数必须来自实际页面或网络证据。网页地址的 page 不一定就是接口分页字段；请求成功也可能返回重复第一页。记录请求参数、响应条数、首尾主键和 next/hasMore 等实际字段。

## JSON 与 DOM 怎么选

实际列表 JSON 通常比页面推荐卡片更容易区分搜索结果。scrape_json_extract 从已捕获 response_id 读取真实数组路径，再按 fields 点路径提取标量；不要猜测不存在的路径。filters 按用户条件制定，unique_by 在跨页保持一致。字段中缺值要检查原响应，不能编造。

## 平台下一页工具

scrape_api_page 只重放同一浏览器里已捕获的只读列表请求，并允许修改已存在的 page/pageSize/offset/limit 整数参数，保持城市和关键词等原条件。它没有任意 URL、请求头、凭据或游标修改入口。原生库或网站有游标分页，不表示这个工具已支持；可以通过实际页面操作继续探索，并说明脚本化能力是否已验证。

## 停止条件与排序

无新主键、真实末页、明确限制或预算不足时停止，保留已采集结果。翻页前后核对原条件，不能把不同地区/分类混合后的集合说成网站默认排序前 N 条。分批筛选时先重置上一项，按 ID/链接去重；重复或空响应不是无限重试的理由。

## 定时脚本

同站 next 链接用实际响应 URL 拼接；crawl 的 parse 返回记录和下一批 URL。脚本最终保存 CSV/JSON/XLSX，核对字段、真实条数和去重规则后再报告。平台只支持公开 GET 的采集运行，实际捕获到 POST JSON 不意味着可直接搬入 collection-v1。
