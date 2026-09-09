# Scrapling 核心接入验证（2026-09-09）

本轮在上一阶段 collection-v1 基础上补充原生 FetcherSession、DynamicSession、匿名会话、并发限速、按页断点和按内容缺失切换动态采集。保留 Python 入口、原队列及独立结果验收。未增加 DP、任意 Spider 目录或 StealthyFetcher。

## 验证环境

独立源码副本 `spiderfly-scrapling-core-nm07sdfx`，Python 3.12，使用已安装后端依赖的独立测试 venv；未读取正式 .env、任务数据库或模型密钥。WSL Ubuntu 单独安装 `spiderfly-collection-v2`：Scrapling 0.4.15 fetchers 依赖、curl_cffi 0.16.3、Playwright 1.62.0 和 Chromium。旧 readonly 环境保留。

## 检查

设置 `SPIDERFLY_TEST_COLLECTION=1`、`SPIDERFLY_TEST_SANDBOX=1`，执行 `python -X utf8 -m unittest discover -s tests -v`：420 项，417 通过，3 项 Windows 符号链接权限跳过，耗时 79.432 秒。

- 真实公开网页使用原生 HTTP 采集，独立核对 CSV 两条书名。
- 原有浏览器 JS、加载资源、点击翻页和定时队列检查通过。
- DynamicSession 使用 Chromium headless shell，真实提取 20 本书的页面。
- 合成 HTTP 服务验证 Cookie 同会话复用、不同会话及不同运行隔离、跨域重定向拒绝、2MB 响应上限和实际并发重叠。
- 每页提交断点；解析下一页故意失败后重试，只请求未完成页。平台级真实采集验证失败验收保留进度，下一次复用结果，验收成功后再次运行重新请求。
- 任务范围和源码变化不会复用旧断点；旧版本断点清理。原结果条件不放宽。

后续补充动作异常不能被原生框架日志吞掉、自动切换不处理访问拒绝的检查，单独运行 test_collection_core，5 项通过，耗时 35.019 秒。原始日志保留于隔离副本，未提交。

## 发布状态及边界

本轮不改前端，使用原有草稿日志及产物展示；没有重新做页面外观验收或真实 DeepSeek 调用。未运行正式业务、发送通知、修改正式数据库或推送 GitHub。上一阶段服务更新被自动审批拒绝，本轮没有重试停止服务；正式后端仍需正常备份、重启后加载。

断点仅保存 JSON 进度和结果，每范围一版、8MB，不保存 Cookie。成功的下一次定时采集从头开始；断点恢复不额外排队或突破时间预算。不同时间网页可能变化，失败页可能再次请求。未完成且不再使用的任务断点目前没有过期清理。
