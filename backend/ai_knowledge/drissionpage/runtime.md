# DP：平台脚本与自动试跑
工具：drissionpage
接入状态：drissionpage-v1 可选原生 Windows 执行器；需要安装固定依赖
主题：创建任务、自动试跑、端口、脚本、部署
版本：DrissionPage 4.1.1.4
核对日期：2026-09-11
适用范围：SpiderFly 当前工作区的 DP 执行规则；原生 Windows 权限，不是安全沙箱
来源：[浏览器启动配置](https://drissionpage.cn/ChromiumPage/browser_opt/)

## 选择与试跑流程
用户指定 DP 时选择 drissionpage-v1；其他场景按需要选择 Scrapling。先调用 dp_probe 检查真实页面，未知选择器传 css:body；再按实际页面证据生成普通 Python 脚本，通过 save_draft 保存，再 test_collection 自动排队试跑。工具返回成功及实际行数才算通过；最多三次，不放宽验收条件。沿用页面“使用草稿”保存任务，再按计划执行；修改已有任务走 submit_task_update。
DP 使用平台 Python 中固定的 DrissionPage==4.1.1.4；部署时在平台虚拟环境安装 backend/requirements-dp.txt，并准备 Edge 或 Chrome。没有准备时明确失败，不在对话中自动安装模型给出的包。不使用 collection-v1 的 spiderfly_collection 接口。
此文件维护当前接入状态。reference/ 和早期概要中的“未接入”是导入时记录，不是当前执行器状态。参考 API 资料版本为 4.1.1.2，实际执行版本为 4.1.1.4。

## 浏览器端口与归属
试跑、版本验证和保存后执行使用同一执行器，读取普通任务的 SPIDERFLY_BROWSER_PORT（默认 9123），不使用 auto_port 或默认 9222。平台启动独立资料目录的无头 Edge/Chrome，确认浏览器启动参数中的资料目录归属，再执行脚本。
脚本只连接 SPIDERFLY_BROWSER_ADDRESS，通过 existing_only 禁止自行另开浏览器。不要 set_local_port、set_user_data_path、auto_port、quit 或新建页签；操作 latest_tab。关闭由平台负责，只结束本次 PID 的进程树。端口占用会等待或明确报错，不切换端口、不结束占用程序。
相同端口不代表沿用上次浏览器或登录态；每次创建新的匿名资料目录。探索工具原有浏览器与此不同，不能把探索成功当作 DP 脚本已经试跑成功。

## 单文件 Python 模板
开头声明运行标记和验收，字段、数量、网址、选择器必须来自用户需求及实际页面证据。这里的网址与选择器只是示例。

```python
# spiderfly-runtime: drissionpage-v1
SPIDERFLY_ACCEPTANCE = {'effects': 'artifacts_only', 'file': 'rows.csv', 'format': 'csv',
    'min_rows': 2, 'max_rows': 2, 'required': ['title'], 'unique_by': ['title'],
    'urls': ['https://books.toscrape.com/']}
import os
import csv
from pathlib import Path
from DrissionPage import Chromium, ChromiumOptions
co = ChromiumOptions(read_file=False).set_address(os.environ['SPIDERFLY_BROWSER_ADDRESS']).existing_only().headless()
page = Chromium(co).latest_tab
page.get('https://books.toscrape.com/')
page.wait.eles_loaded('css:h3 a', timeout=15)
rows = [{'title': item.attr('title')} for item in page.eles('css:h3 a')[:2]]
with (Path(os.environ['SPIDERFLY_ARTIFACT_DIR']) / 'rows.csv').open('w', newline='', encoding='utf-8-sig') as stream:
    writer = csv.DictWriter(stream, fieldnames=['title'])
    writer.writeheader()
    writer.writerows(rows)
```

## 权限与验收
这是普通 Windows Python 子进程，代码具有宿主机账号权限；清理环境变量和临时目录不构成安全沙箱。只面向已授权的任务源码，不用于运行来源不明代码。域名声明约束需求及网络证据，不是原生 Python 的网络防火墙。
平台单独记录目标站点实际响应并检查产物字段、行数、唯一键等冻结条件。退出码为零但无网页响应、无文件或数量不符仍失败。日志记录实际地址；停止或超时结束本次进程树。不能凭通过有限验收就宣称满足未声明的业务规则。
