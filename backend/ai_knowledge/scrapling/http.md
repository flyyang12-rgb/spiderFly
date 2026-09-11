# HTTP 抓取、响应与安装
工具：scrapling
接入状态：已有受控适配；具体原生功能是否开放以正文和平台接口为准
主题：http

版本：Scrapling 0.4.15；项目锁定版本
核对日期：2026-09-11
适用范围：原生 Fetcher 参考；平台公开 GET 子集
来源：[docs/fetching/static.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/fetching/static.md)
来源：[docs/fetching/choosing.md](https://github.com/D4Vinci/Scrapling/blob/333fa22b7a5821194ce66b59b11f4b16a6484f02/docs/fetching/choosing.md)

关键词：Fetcher FetcherSession AsyncFetcher HTTP GET POST 安装 依赖 状态码 请求头 编码 TLS。

## HTTP 适用场景

服务端返回的 HTML 或 JSON 已包含目标字段时，先用 HTTP，通常比启动浏览器开销小。返回 200 后还要查看正文：加载占位、验证码、登录提示都可能返回 200。空结果不等于站点无数据。读取响应的 URL、状态及内容，再选择解析方式。

## 原生安装与平台版本

只安装 scrapling 的基础包主要提供解析功能；Fetcher 等额外能力需要 fetchers 依赖，浏览器还需要对应运行文件。本项目依赖在 backend/requirements-browser.txt，固定 scrapling[fetchers]==0.4.15。WSL 采集环境由 backend/sandbox/setup_collection.py 准备，与 Windows 的安装分开。解释安装时按使用环境给步骤，不通过临时升级依赖修复未知错误。

## 原生 HTTP 示例

以下是独立 Python 环境的 API 用法，不是已经执行的网络结果，也不是 collection-v1 的网络入口。

```python
from scrapling.fetchers import Fetcher
page = Fetcher.get('https://example.com/', timeout=20)
print(page.status)
print(page.css('title::text').get())
```

原生 Fetcher 还具有请求方法、会话与请求配置能力；collection-v1 通过平台 broker 提供公开 GET，不因库支持 POST 就认为受限脚本可以调用 POST。原生 HTTP timeout 与浏览器参数的时间单位应分别核对，不能把 20 秒写成浏览器 20 毫秒。

## 平台调用方式

AI 探索：scrape_page(mode='auto') 先 HTTP，实际缺少动态内容再考虑无头浏览器。脚本：from spiderfly_collection import get，get(url) 返回可用 CSS/XPath 的 Selector；fetch 提供受控自动渲染选择。TLS 错误先核对证书和环境，不把 verify=False 当默认方案。站点返回拒绝或限流要根据响应处理，不能不断换引擎伪装成成功。
