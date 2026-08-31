# SpiderFly

第一次使用或只想看简单步骤，请先阅读：[《SpiderFly 使用说明（大白话版）》](使用说明.md)。

SpiderFly 是一个 **Python-only 的共享 RPA 调度中心**：在一台常开 Windows 电脑上集中保存、安装和运行 Python 任务，3～5 位伙伴只需使用浏览器访问。伙伴电脑不需要安装 Python，也不需要远程登录这台主机。

## 适合的使用方式

- 一台 Windows 主机保持开机并运行一个 SpiderFly 服务进程。
- 所有手动运行和计划运行都进入同一个持久化队列，全局一次只执行一个 Python 任务；服务重启后，尚未完成的排队任务仍可继续处理。
- 每个任务拥有独立虚拟环境，任务之间的 Python 依赖互不影响。
- 每位伙伴使用自己的账号。管理员负责账号和任务管理，普通成员按授权使用任务；操作和执行结果留有记录。
- 脚本、依赖、日志和数据库都保存在共享主机，日常操作通过网页完成。

## 控制台结构

界面借鉴成熟 RPA 控制台的“任务—执行”组织方式，但不依赖或复制影刀应用代码：

- **工作台**：查看当前运行、等待数量和最近记录；
- **任务中心**：统一查看、运行和设置任务；环境准备失败时可在这里修复，任务不用时也从这里彻底删除；
- **运行中心**：通过“任务时间表”和“运行记录”两个页签查看计划与结果；时间表可切换今日、本周，直接展示每日和每周任务的安排，工作台继续保留当前队列摘要；
- **管理中心**：仅管理员可见，用于创建任务、管理成员、查看系统状态和操作审计；不再单独维护“程序列表”。

当前版本不导入影刀本机应用。影刀应用通常依赖 `xbot`、`xbot_visual` 等内部运行时，不能当作普通 Python 文件直接运行。AI、知识库和成功/失败后的自动处置也暂不包含在本期范围内。

> SpiderFly 依靠单个服务进程保证调度协调。日常只运行 `SpiderFly.exe`，不要自行添加 Uvicorn `--workers` 参数。

`SpiderFly.exe` 会无黑窗启动服务、等待健康检查并把日志写入 `data/logs/spiderfly.log`。`start.bat` 只保留为首次准备和开发维修入口。任务不需要再配套 `.bat`、`.cmd` 或额外启动器；执行器会直接调用该任务独立虚拟环境中的 Python，并以进程退出状态和可选 `result.json` 记录结果。

后端在任何数据库初始化和中断恢复之前获取机器级单实例锁，因此从批处理、直接 Uvicorn 或其他入口重复启动时，第二个调度器都会先被拒绝，不会改写正在运行的任务状态。锁由操作系统在进程退出或崩溃时自动释放。

## 首次安装与启动

共享主机需要预先安装：

- Python 3（安装时勾选“Add Python to PATH”）；
- Node.js 与 npm（仅用于安装和构建网页前端）。

首次准备仍运行一次：

```text
start.bat
```

首次运行会自动完成以下工作：

1. 创建 SpiderFly 自己的 `.venv`；
2. 安装后端依赖；
3. 安装并构建前端；
4. 初始化数据库、持久队列和管理员账号；
5. 以单进程、单 worker 方式启动服务。

准备完成后，运行 `scripts/build_launcher.ps1` 生成根目录下的 `SpiderFly.exe`。日常只需双击这个 EXE；需要当前固定 RPA Windows 账号登录后自动启动时，运行一次 `scripts/install_autostart.ps1`。它采用交互用户计划任务，不会安装为 Windows 服务。

当前这一阶段的 EXE 是轻量启动器，仍使用项目自己的 `.venv`。要复制到一台完全没有 Python/Node 的新电脑，还需要后续制作包含私有完整 Python 的正式安装包，不能直接复制本机 `.venv`。

主机本机访问：

```text
http://127.0.0.1:8000
```

首次启动生成的管理员账号和随机密码保存在：

```text
data/首次登录信息.txt
```

请只在共享主机上查看该文件。管理员首次登录后必须修改密码，修改成功后该文件会自动删除；随后为每位伙伴建立独立“操作员”账号，不要多人共用管理员账号。`data/` 和 `.env` 已被 Git 忽略，不要把首次登录文件或数据库发送给他人。

如果修改了 `SPIDERFLY_DATA_DIR`，首次登录文件和数据库会改存到所配置的数据目录。

## 伙伴如何连接

### 同一局域网

SpiderFly 只需安装在一台共享 RPA 主机上。伙伴电脑无需安装项目、Python 或任务依赖，只需使用浏览器和各自的普通账号访问共享主机；任务最终都在共享主机上排队运行。任务创建仍由管理员负责。

若 Windows 防火墙当前被关闭或由公司安全软件接管，应先让 IT 按企业策略启用防护并添加等价的“可信内网 TCP 8000”规则。当前能访问不代表安全配置已经完成。

第一次共享前，在主机上双击 `开启局域网访问.bat`。Windows 会弹出管理员确认；确认当前连接的是可信的公司、家庭网络或自己的热点后，输入 `Y`。脚本只为“专用网络”的本地子网放行 TCP 8000，不会创建公网端口映射。以后端口仍为 8000 时不需要重复运行。

在共享主机运行 `ipconfig`，找到当前网卡的 IPv4 地址，例如 `192.168.1.50`。伙伴在浏览器中访问：

```text
http://192.168.1.50:8000
```

也可以先尝试启动窗口显示的电脑名称地址，例如 `http://OFFICE-PC:8000`；如果伙伴电脑无法解析该名称，就改用 IPv4 地址。

建议在路由器或网卡设置中为共享主机固定该地址。如果之后更换端口，请用相同端口重新运行 `configure_lan_access.ps1`；不要为公用网络放行，也不要在路由器上做公网端口映射。

### 不在同一网络

推荐在共享主机和伙伴电脑上加入同一个 Tailscale 团队私网，再通过共享主机的 Tailscale 地址访问。不要把 SpiderFly 的 8000 端口直接暴露到公网；如需正式公网访问，应另行配置 HTTPS、反向代理和更严格的访问控制。

## 创建任务、Python 环境与依赖

在网页的“管理中心 → 创建任务”中，一次填写任务名称、说明、入口 `.py`、Python 依赖、可选 Excel 模板、触发方式和时间、启用状态，以及成功和失败通知。点击一次“创建任务”，SpiderFly 就会保存全部设置并在后台准备该任务专用的独立 `.venv`，创建后不用再去“编辑任务”补内容。脚本使用第三方 Python 包时，可选择 UTF-8 编码的 `requirements.txt`，也可直接按每行一个依赖手填；系统不会从源码的 `import` 自动推断安装包，例如使用 DrissionPage 时必须明确填写 `DrissionPage==4.1.1.4`。任务的程序文件和模板保存在 `data/apps/`，独立虚拟环境保存在 `data/envs/`。环境只安装已声明的依赖，显示“Python 就绪”后才能运行；首次安装可能需要等待下载。以后需要调整运行设置时，才到“任务中心”编辑。已上传的脚本、依赖和模板不能在编辑中替换，需要更换时请删除旧任务后重新创建。

所有任务共用 `SPIDERFLY_WORK_DIR` 指定的唯一公共文件夹。领取队列首项前，SpiderFly 会检查 Excel 进程和只绑定本机的专用浏览器端口（默认 `127.0.0.1:9123`）；资源仍被占用时任务保持 pending。日常打开 SpiderFly 管理页面的 Chrome/Edge 不使用该端口，因此不会挡住自己发起的任务。宿主机空闲后才清空公共目录、复制可选模板并启动 Python。实际运行统一最多 600 秒，排队时间不计入；成功、失败、超时或取消后都会再次清理公共目录。平台不会按进程名强杀别人的 Excel 或浏览器。

环境修复采用新目录构建、校验通过后再切换的方式，不会覆盖当前环境。修复期间或新环境构建失败时，只要原环境仍完整，已有任务仍可继续使用原环境运行。切换成功后，系统会删除不再使用的旧环境，但会保护排队中或运行中执行记录所引用的环境。构建会分别限制创建 venv、安装依赖和最终校验的时长；超时或服务停止时，只终止本次构建的进程树，并清理尚未发布的候选环境。

### 任务文件与删除规则

一个任务对应一份程序文件和一个独立 `.venv`。每次点击“创建任务”都会一次性建立完整任务，不需要创建后再编辑补全。后续“编辑任务”只用于改变运行设置，不能替换脚本、依赖或模板。

管理员删除任务时，会同时删除任务的全部执行记录和 `data/executions/` 资料、程序脚本、Excel 模板、`requirements.txt`、程序目录、全部对应 `.venv` 以及数据库中的全部对应记录。正在运行的任务必须等运行结束后再删除；排队中的任务会直接从队列清除。

没有独立的“程序删除”入口，也不会让管理员处理“未绑定程序”。任务不用时，直接在“任务中心”删除即可；以后可以用相同名称重新创建一个全新任务。

一个简单的依赖文件示例：

```text
DrissionPage==4.1.1.4
pandas==2.3.2
openpyxl==3.1.5
```

建议锁定经过验证的依赖版本。不要在运行脚本时临时执行 `pip install`，也不要让不同任务共用一个虚拟环境。

浏览器、Office、OCR、数据库客户端和系统驱动等 **非 Python 组件** 不在 `requirements.txt` 管理范围内，需要由管理员统一安装在共享主机上。如果依赖来自公司内网源或私有仓库，也需要先为共享主机配置相应网络和凭据。

## 桌面 RPA 注意事项

如果脚本会点击桌面、浏览器或 Office：

- 共享主机必须保持对应 Windows 用户已登录；
- 关闭自动睡眠和休眠；
- 执行期间不要锁屏、切换用户或让远程桌面抢占会话；
- 避免伙伴直接操作共享主机桌面，日常运行统一通过 SpiderFly 网页发起。

纯后台脚本不受桌面会话限制。

## 执行资料与结构化结果

每次运行都会获得独立的 `data/executions/<执行编号>/` 目录。SpiderFly 不改变旧脚本的工作目录，同时向脚本提供以下环境变量：

```text
SPIDERFLY_EXECUTION_ID
SPIDERFLY_EXECUTION_DIR
SPIDERFLY_RESULT_FILE
SPIDERFLY_ARTIFACT_DIR
SPIDERFLY_DOWNLOAD_DIR
SPIDERFLY_SCREENSHOT_DIR
SPIDERFLY_TMP_DIR
```

普通脚本可以完全忽略这些变量，仍按退出码判断成功或失败。需要区分业务结果、提供人工处理入口时，可在 `SPIDERFLY_RESULT_FILE` 指向的位置写入 UTF-8 JSON：

```json
{
  "schema_version": 1,
  "outcome": "manual_required",
  "code": "LOGIN_EXPIRED",
  "message": "登录状态已失效，请重新登录后再运行",
  "retryable": false,
  "manual_action_url": "https://example.com/login",
  "manual_code": "ACCOUNT_RELOGIN"
}
```

`outcome` 支持 `success`、`failure` 和 `manual_required`。进程超时、被取消或退出码非 0 时，脚本不能用 JSON 把它覆盖为成功；退出码为 0 但 JSON 声明业务失败时，执行仍会正确记录为失败。SpiderFly 只向 DrissionPage 应用提供本次运行专属的下载、截图和临时目录；脚本仍需显式读取这些环境变量并配置给 DrissionPage，浏览器不会自动改用这些目录。

## DrissionPage 与影刀的使用边界

可从 [`examples/drissionpage_managed_template.py`](examples/drissionpage_managed_template.py) 复制一个自管浏览器应用，在 requirements 中加入 `DrissionPage~=4.1`。模板读取 `SPIDERFLY_BROWSER_PORT`（默认 9123），同时使用公共工作区中的一次性独立用户目录，因此可以和人工打开的管理 Chrome 并存；它还会主动设置本次执行的下载和截图目录，并在结束时只关闭自己创建的浏览器。端口 9123 只供 SpiderFly 使用，并且只绑定 `127.0.0.1`，不要暴露到局域网。

若必须复用由影刀或人工打开的登录浏览器，应另写“接管模式”：使用明确的调试地址和 `existing_only()`，只关闭本任务新建的标签页，不调用 `browser.quit()`。专用端口只能协调 SpiderFly 自己的浏览器，暂时不能判断影刀是否正通过其他端口运行网页任务；两边同时运行时仍需人工错峰。SpiderFly 不会替代影刀控制台。建议让 DrissionPage 负责稳定的网页 DOM、下载和截图操作，让影刀继续负责桌面软件、Office、系统窗口以及必须人工配合的流程。

## 配置

无需配置即可启动。需要调整时，将 `.env.example` 复制为 `.env`。常用配置如下：

```env
SPIDERFLY_HOST=0.0.0.0
SPIDERFLY_PORT=8000
SPIDERFLY_DATA_DIR=data
SPIDERFLY_APPS_DIR=data/apps
SPIDERFLY_ENVS_DIR=data/envs
SPIDERFLY_VENV_TIMEOUT_SECONDS=180
SPIDERFLY_PIP_TIMEOUT_SECONDS=1800
SPIDERFLY_ENV_VERIFY_TIMEOUT_SECONDS=60
SPIDERFLY_SESSION_HOURS=12
SPIDERFLY_COOKIE_SECURE=false
```

`0.0.0.0` 表示允许局域网网卡接收连接，浏览器中仍应填写 `127.0.0.1` 或主机实际 IP。直接使用 HTTP 时保持 `SPIDERFLY_COOKIE_SECURE=false`；只有在 HTTPS 反向代理配置正确后才设为 `true`。

`SPIDERFLY_BASE_PYTHON` 可指定用于创建各任务虚拟环境的 Python 解释器。路径包含空格时可以写成：

```env
SPIDERFLY_BASE_PYTHON=C:\Program Files\Python313\python.exe
```

飞书通知是可选功能；不填写也不影响排队、计划运行和日志查看。需要时再补充 `.env.example` 中的飞书配置。

## 数据与备份

需要备份的核心目录是：

```text
data/
```

其中包括 `spiderfly.db`、任务文件、任务虚拟环境、执行资料和首次登录信息。建议每天把 `data/` 复制到另一块磁盘或受控的备份位置，并定期验证能否恢复。为得到一致备份，最好先停止 SpiderFly，再复制 `data/`；至少要确保数据库文件在备份过程中没有被写入。

如果把数据、任务文件或环境目录改到了 `data/` 之外，也要将相应的自定义目录纳入备份。

`.venv/`、`frontend/node_modules/` 和 `frontend/dist/` 都可以通过 `start.bat` 重新生成，通常不必备份。任务虚拟环境也可以根据 `requirements.txt` 重建，但保留整个 `data/` 最省事。

## 日常维护

- 日常只双击 `SpiderFly.exe`；重复双击只会打开现有页面，不会启动第二个调度器。
- 登录自启通过 `scripts/install_autostart.ps1` 安装；取消时运行 `scripts/uninstall_autostart.ps1`。
- `start.bat` 只用于首次准备、依赖修复或前端重新构建。
- 程序或依赖要更换时，在任务中心删除原任务，再到管理中心创建新任务。
- 安装来源不明的 Python 包和脚本具有风险，只允许可信管理员创建任务。
- 不要用 `--reload` 或多 worker 模式运行生产调度服务。

## 开发模式

后端开发：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

前端开发：

```powershell
cd frontend
npm run dev
```

开发模式只供本机调试，不应承担共享任务调度。

## 目录

```text
spiderFly/
├── backend/             FastAPI、账号权限、持久队列与本地执行
├── frontend/            浏览器管理界面
├── examples/            可复制的 Python 任务脚本模板
├── data/                数据库、任务文件、环境和运行资料（运行后生成）
│   ├── apps/            创建任务时上传的脚本、依赖和模板
│   ├── envs/            每个任务的独立虚拟环境
│   └── spiderfly.db     SQLite 数据库
├── 共享工作区/          唯一公共工作文件夹（运行前后自动清空）
├── SpiderFly.exe        日常无黑窗启动入口
├── launcher/            启动器源码
├── scripts/             启动器构建与登录自启脚本
├── .env.example         可选配置模板
├── start.bat            首次准备与开发维修入口
└── 开启局域网访问.bat   首次共享时的一键安全配置
```
