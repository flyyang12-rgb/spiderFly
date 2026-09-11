# SpiderFly 单文件任务约定

核对日期：2026-09-08。Python 3.12。一个任务上传一个 UTF-8 `.py` 入口，依赖单独声明。
现有平台串行执行任务，工作目录是上传脚本所在目录。公共工作区运行前后会清空。

可用环境变量：
- SPIDERFLY_ARTIFACT_DIR：本次运行的结果目录，保存可下载结果。
- SPIDERFLY_RESULT_FILE：本次 result.json 的绝对路径。
- SPIDERFLY_TEMPLATE_FILE：上传的 Excel 模板或输入文件；没有上传时为空。
- SPIDERFLY_EXECUTION_ID：本次执行编号。

最小回执格式：
```json
{"schema_version":1,"outcome":"success","code":"TASK_DONE","message":"按实际检查结果填写","retryable":false}
```
outcome 支持 success/failure/manual_required。人工介入需提供 manual_code 或 manual_action_url。
先验证文件、字段、筛选条件和数量，再写 success；仅进程退出码为零不能证明业务成功。
输出内容不得捏造，不把空表一律判为成功或失败，按用户原目标判断。失败保留已保存的结果并抛出明确异常。
普通草稿只做静态检查。声明 collection-v1 或 drissionpage-v1 的公开采集草稿可通过 test_collection 真实试跑，按工具返回状态说明结果；先检索公开网页采集知识。

推荐使用标准库 json/pathlib/os 和明确声明的第三方依赖。不要导入 app 或本机其他脚本，不读取平台配置、密钥和数据库。
需要本地秘密时通过用户配置的环境变量引用；不能把凭据硬编码。
参数先作为顶部有说明的常量。当前任务参数仍写在源码；需求与来源由平台随版本保存。已有任务明确变更时保存草稿，用 submit_task_update 提交 draft_id、read_task 返回的 task_requirements.version_id 及 spec_patch（仅用户明确变更的字段，修复传 {}）。候选版本排队或验证通过都不代表已启用，用户确认使用后才切换当前版本。不能直接更改计划或虚构参数表单 API。

Excel 保存及 Excel/浏览器关闭由脚本负责，平台不会自动接管。按业务要求保存结果，异常保留错误，在 finally 中关闭本次创建的资源；不要全局结束用户已有窗口。

网页探索：优先使用 scrape_search、scrape_page、scrape_inspect、scrape_extract 调用原生 Scrapling HTTP/无头引擎并提取 CSV。不先要求登录或打开可见浏览器。确实需要交互时才使用独立 Windows Chromium；登录由用户在宿主机窗口完成，点击继续后沿用会话。先读 scraping.md；不要混淆原生探索工具与 collection-v1 定时脚本的能力。

DP 采集：先读 drissionpage/runtime.md，通过 dp_probe 在普通任务配置端口观察页面，再生成原生 Python、save_draft、test_collection。DP 浏览器由平台按本次进程归属开关，脚本不自行 quit。
