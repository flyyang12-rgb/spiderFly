# SpiderFly Windows Agent

本目录可独立复制到第二台 Windows 10/11 电脑，只需要 Python 3.12 和本目录依赖，不需要主控源码、数据库、模型配置或知识库。主控与 Agent 通过协议 v1 通信，完整协议位于 SpiderFly 源码的 `docs/Agent协议_v1.md`（下载包不包含主控开发文档）。当前支持普通单文件 Python 的指定宿主机手动和每天/每周运行、Windows 托盘及运行状态条。

## 接入

小白用户先在主控网页“宿主机”中生成接入码并下载 Agent 安装包。在已登录且未锁屏的 Windows 用户会话中解压 ZIP，双击 `安装并接入Agent.bat`，填写主控地址、接入码和电脑名称。向导会检查 Python 3.12 与主控连接，可选择登录 Windows 后自动启动；申请成功后等待管理员在主控网页批准，不需要打开 PowerShell。

需要诊断或自动化安装时，也可以打开 PowerShell，进入本目录后执行：

```powershell
.\start.ps1 -Server http://192.168.1.50:9356 -Code '网页生成的接入码' -Name '办公电脑 A'
```

首次启动建立本目录 `.venv`，安装 Agent 依赖并申请注册。管理员批准前不会接收任务；接入码有效期 20 分钟，一次成功申请后消费。日常运维统一使用 `manage.ps1`；托盘默认保持非调度连接，可从托盘开启调度，也可以启动时直接开启：

```powershell
.\manage.ps1 Start -Dispatch
```

主控网页和本机托盘都开启调度，且当前 Windows 会话可交互时，才领取指定给本机的任务。锁屏、注销、RDP 断开时不领取；运行期间检测到桌面不可用会请求停止本次进程。任务运行时屏幕顶部显示任务、进度、耗时、停止和紧急接管。运行中不能直接切回非调度；“紧急接管”会先停止本次进程树，确认结束后再关闭本机调度。

当前状态条只做提示和安全接管，不限制键盘鼠标，也不黑屏或锁屏。输入限制要等独立看门狗和异常自动解除一起实施。需要诊断纯命令行模式时使用 `.\manage.ps1 Start -Console`；此模式可按 Ctrl+C 停止 Agent 并等待本次进程树收尾。

系统浏览器、Office 和业务客户端由使用者准备。每个任务包按脚本与依赖内容哈希创建本机 Python 环境，第一次实际运行时安装 `requirements.txt`；依赖安装计入本次超时，也可以被停止。

## 日常运维入口

```powershell
.\manage.ps1 Start                 # 后台启动，默认非调度
.\manage.ps1 Start -Dispatch       # 后台启动并打开本机调度
.\manage.ps1 Status
.\manage.ps1 Logs                  # 最近日志；持续跟随增加 -Follow
.\manage.ps1 Stop                  # 文件式安全停止，最多等待 90 秒，不强杀
```

`Stop` 会让 Agent 停止当前 worker、等待进程树收尾并尽力补传结果；超时后只报错，不按名称或 PID 强杀未知进程。运行中需要立即拿回桌面时，优先使用托盘“紧急接管”。后台启动日志位于数据目录的 `logs/agent.out.log` 和 `logs/agent.error.log`。

同一台主控只改变局域网地址时，先确认 Agent 已停止且 journal 全部回传，再执行 `.\manage.ps1 SetServer -Server http://新地址:端口`；命令会校验地址并保存旧配置。更换为另一台主控不能使用此入口，必须保留原数据目录并用新数据目录重新注册。

升级必须由管理员从主控网页下载新 ZIP 后显式执行，Agent 不自动下载或覆盖程序：

```powershell
.\manage.ps1 Stop
.\manage.ps1 Backup -BackupPath D:\Backups\agent-before-upgrade.zip
.\manage.ps1 Upgrade -PackagePath D:\Downloads\SpiderFlyAgent-0.3.0.zip
.\manage.ps1 Start
.\manage.ps1 Status
```

升级只替换固定程序清单，保留数据目录、机器身份和 journal，并在 Agent 目录旁保存旧程序 ZIP。主控协议仍为 v1，但当前最低 Agent 为 0.3.0；过旧或协议不兼容时，主控拒绝注册/心跳和任务领取，错误会要求人工升级。

## 独立安装与命令

也可以自行准备环境后运行模块：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m spiderfly_agent setup --server http://192.168.1.50:9356 --code '接入码'
.\.venv\Scripts\python.exe -m spiderfly_agent run --dispatch --desktop
```

`start.ps1` 是底层启动器。它根据 `requirements.txt` 的内容哈希检查依赖，仅在安装和 `pip check` 都成功后保存就绪标记；首次安装中断、失败或依赖清单变化时，下次启动自动重新安装。主控地址可配置为 HTTP 局域网地址或 HTTPS；Agent 不使用环境代理，不跟随重定向。HTTP 签名校验身份和内容，但不加密代码与日志，仅适用于可信局域网。

默认运行资料保存在当前用户 `%LOCALAPPDATA%\SpiderFlyAgent`。可给 `start.ps1` 指定 `-DataDir`，或给模块指定 `--data-dir`（放在 setup/run 之前）。Windows 的机器级互斥量保证同一物理电脑只能运行一个执行端，不因数据目录、账号会话或端口变化绕过。首个增量与主控旧的本机执行器共用此锁，因此 Agent 必须在第二台电脑运行；旧本机执行器迁移完成后才能支持主控电脑安装 Agent。已有身份不能直接切换到另一个主控，换主控应使用新的数据目录重新申请。

## 数据与执行约束

| 目录或文件 | 用途 |
| --- | --- |
| `identity.json` | UUID 和当前 Windows 用户 DPAPI 加密的 Ed25519 私钥 |
| `config.json` | 主控地址、宿主机 ID 和名称 |
| `journal.sqlite3`、WAL/SHM | 已接收运行、单调事件序号、未回传日志和产物状态 |
| `envs/<内容哈希前 32 位>/` | Python 环境；`.package-hash` 保存并校验完整哈希，准备成功后才写就绪标识 |
| `runs/<运行 ID>/` | 冻结的 `main.py`、独立工作目录、inputs、artifacts |

运行脚本可通过 `SPIDERFLY_ARTIFACT_DIR` 写结果文件，通过 `SPIDERFLY_RESULT_FILE` 写可选 JSON 回执。首个增量只上传 artifacts 目录直接包含的普通文件，每个最大 16 MiB、每次最多 100 个文件、合计最多 64 MiB；不递归目录，不上传符号链接或目录外文件。产物上传完成后才回传终态；网络失败保留待回传记录并使用新签名重试。stdout/stderr 实时保存并按序补传，原始 `SPIDERFLY_PROGRESS` 行保留在日志中。

子进程在放行运行前加入 Windows Job Object 并持久登记 PID；停止、超时和正常收尾只处理本次进程组，不按名称清理用户软件。子进程树全部结束才生成终态。Agent 崩溃时 Windows 关闭 Job Object 并终止组内进程；重启后不会重新执行已登记的运行 ID。若恰逢进程登记窗口、权限不足或无法证明旧进程已结束，保持“状态待确认”，不接新任务。此增量不提供绕过核对的一键强制释放。

文件名不安全、大小写折叠冲突、超限或主控永久拒绝的产物会记录具体错误，不无限重试。脚本原本成功但产物不完整时，整次运行报告失败，保留实际 Python 退出码；网络中断和服务端临时错误仍保留重试，心跳与停止查询继续进行。

环境目录使用短哈希前缀，避免完整哈希叠加 pip 深层目录触发 Windows 路径长度限制；完整哈希不同则拒绝复用。自定义数据目录仍应保持简短，例如 `D:\SpiderFlyAgent`。不会修改 Windows 的长路径策略。

Agent 和普通 Python 任务使用当前 Windows 账号权限，Job Object 用于生命周期管理，不是安全沙箱。备份身份和 journal 时先停止 Agent；DPAPI 身份不能跨机器或 Windows 账号直接复用。不要在还有未回传事件时删除数据目录；自动保留期限和清理策略将在后续阶段加入。

备份与恢复命令：

```powershell
.\manage.ps1 Stop
.\manage.ps1 Backup -BackupPath D:\Backups\SpiderFlyAgent-state.zip
# 只有原目录已改名保留、目标目录为空时才恢复
.\manage.ps1 Restore -BackupPath D:\Backups\SpiderFlyAgent-state.zip -ConfirmRestore
```

默认备份身份、配置、journal、未回传运行和产物；加 `-IncludeCaches` 才包含可重建且可能很大的 `envs/`。ZIP 带 SHA-256 清单。恢复拒绝覆盖非空目录，也不会启动 Agent。`identity.json` 受 DPAPI 保护，因此恢复机器身份只适用于同一台 Windows、同一账号；换电脑、换账号或换主控必须新建数据目录并重新申请。

## 合成验证

把 `agent/` 复制到独立临时目录并在副本运行以下命令，禁止导入正式主控 app。测试只创建临时文件和合成子进程；不运行真实业务任务。

```powershell
python -m pip install -r requirements.txt
python -X utf8 -m unittest discover -s tests -v
```

测试覆盖签名、重放、连续序号、单实例、DPAPI、停止、超时、紧急接管、调度切换、结构化进度、依赖准备失败、后台子进程收尾、产物顺序和重启占用。两台真实 Windows 电脑的接入、桌面软件运行和网络断开验收需另行记录，自动测试不能替代。
