# 公开网页采集

核对日期：2026-09-08。Scrapling 官方文档：https://github.com/d4vinci/Scrapling
当前已用本机环境验证的基础版本：scrapling[fetchers]==0.4.15。

基础接口：
```python
from scrapling.fetchers import Fetcher
page = Fetcher.get(url)
titles = page.css('h3 a::attr(title)').getall()
```
选择器必须依据实际网页；没有访问成功时标注未验证。不得把示例选择器当成所有网站通用规则。
Scrapling 可用 DynamicFetcher 调用浏览器，但安装 pip 依赖不等于浏览器已安装。
`from scrapling.fetchers import DynamicFetcher`，`DynamicFetcher.fetch(url)`；真实浏览器环境需另外准备并检查。
单次调通、页面读取或 README 的反检测介绍，都不能证明能够稳定突破目标站点风控。

采集流程：核对站点/城市/条件 → 读取列表 → 获取详情 → 以稳定 ID/链接去重 → 校验字段 → 导出。
逐页保存增量结果和待处理位置，有限重试。数据不够时记录实际数量及原因，不能放宽用户条件凑数。
薪资排序要保留原文，明确是月薪上限、下限还是年薪；不要混淆按天、按月和多薪制。
保存职位/商品来源链接、采集时间、访问失败记录；公开列表信息不等于当前仍在招/有库存。

本机过往招聘试验：普通 Scrapling 请求曾被 BOSS 安全页拦截，部分公开列表可由浏览器读取；未验证稳定风控绕过。
这个经验仅说明当时观察，不是当前所有 BOSS 页面都可访问的保证。遇登录/验证码，保留进度和原因。

持有浏览器实例的脚本应在 finally 中退出本次创建的实例；不要退出附着的个人浏览器或按进程名全局终止。需要错误截图时先截图，再关闭并保留原异常。平台不会自动接管任意上传脚本的浏览器。
