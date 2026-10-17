# Agent 协议 v1（首个实施增量）

本协议用于局域网宿主机接入和指定机器手动执行。主控沿用 SQLite，Agent 使用独立本地 SQLite 日志；只有主控访问中心数据库。现有单机队列暂保留，远程运行不进入旧本机执行或自动修复队列。完成迁移前，网页分别显示本机与远程运行。

## 身份与通信

Agent 生成 Ed25519 密钥对，私钥仅存本机（Windows DPAPI）。管理员通过会话认证生成接入码，20 分钟有效；接入码在首次成功申请时原子消费，避免两台机器同时申请。重复申请相同机器 ID 和公钥返回原申请，不能绑定其他公钥。管理员批准后，机器 ID 与公钥构成长期身份；撤销后所有机器接口拒绝访问。

使用可配置 `http://<局域网主控>:9356` 或 HTTPS 地址；v1 使用 HTTP 轮询。签名验证身份与请求完整性，HTTP 不加密内容，因此仅用于可信局域网。HTTPS 使用同一协议。Agent 不跟随跨地址重定向。

注册：`POST /api/agent/register`，JSON 为 `enrollment_code, machine_id, name, public_key, agent_version, protocol_version`。公钥为 32 字节 Ed25519 公钥的 Base64。返回 `{host_id, approval_status, protocol_version:1, minimum_agent_version, current_agent_version}`。

协议号和 Agent 程序版本分别校验。协议号不同，或 Agent 低于主控公布的最低版本时，注册/心跳返回 409，且不会消费接入码、更新宿主机状态或下发任务。当前协议为 v1，普通运行最低 Agent 为 0.3.0；0.5.0 起支持超级管理员显式发起的远程升级，旧版需从宿主机页下载新包后在本机引导一次。不允许盲目追随最新版。

后续所有机器请求携带 `X-SpiderFly-Host`（整数）、`X-SpiderFly-Time`（Unix 秒）、`X-SpiderFly-Nonce`（随机 UUID hex）、`X-SpiderFly-Signature`（Base64）。签名原文为以下字段使用换行拼接的 UTF-8 字节，无末尾换行：

```text
timestamp
nonce
METHOD
/api/agent/path
sha256(raw_request_body)
```

只接受服务器时间前后 90 秒，nonce 在该窗口内不可复用。所有请求签署实际传输的原始字节。路径使用 URL 解码后的 Unicode 路径，不含主机和查询参数；本协议机器接口不使用查询参数。空 GET body 的哈希是空字节 SHA-256。

## 心跳、领取与状态

`POST /api/agent/heartbeat`：`{protocol_version:1, agent_version, dispatch_enabled, interactive_session, active_run_id:null|int, recovery_required:false}`。返回 `{host, run:null|{id,task_name,timeout_seconds,package_hash}, stop_requested:false, protocol_version, minimum_agent_version, current_agent_version}`。

Agent 默认非调度。服务器管理员可设置主控侧 `dispatch_enabled`；本机也必须开启调度且有交互会话才领取任务。任一侧关闭时不领取新任务；运行期间关闭主控调度返回 409，须先停止。每台机器最多一个非终态租约。指定机器离线/非调度/忙碌时保持原目标排队。

心跳返回的运行只是 offer，不是执行授权。Agent 必须先把运行 ID 和包哈希持久写入本地 journal，再调用 `POST /api/agent/runs/{id}/claim`。主控记录 `claimed_at` 后才返回执行描述，随后才允许下载任务包。claim 可用相同本地登记幂等重试；普通 409 不能当作取消。

心跳超过 15 秒显示离线。尚未 claim 的 offer 不可能执行，可以安全回到队列；Agent 已持久登记但 claim 请求尚未到达时，重连报告相同 ID 后重新挂接。claim 后失联的运行仍显示状态待确认且不释放占用；重连时相同运行继续上报事件，无法证明旧进程结束则报告 `recovery_required:true`。协议不承诺外部业务副作用的 exactly-once，只确保同一运行 ID 不重复启动子进程。

已知恢复边界：执行授权已经返回后，若 Agent 本地 journal 被人为删除或损坏，主控仍会保守保持 uncertain；当前不提供跳过进程核对的强制释放。首次 offer/claim 响应丢失的正常协议恢复已经覆盖，不再需要人工释放。

`GET /api/agent/runs/{id}/package` 只对完成 claim 的当前租约返回 `{script,requirements,package_hash}`，其中 hash 为 `sha256(UTF8(script) + b'\0' + UTF8(requirements))`。当前增量把运行绑定到不可变 `code_version_id` 和 `version_sequence`，拒绝模板文件和非普通 Windows 运行环境；通用输入/凭据迁移在后续阶段接入。

若管理员在领取后、下载前请求停止，此接口向当前租约所属 Agent 返回 `{stop_requested:true}`，不返回脚本；Agent 持久记录 cancelled 终态并回传确认，不创建环境或启动脚本。其他 409 不能一概当作取消。

`POST /api/agent/runs/{id}/events`：`{events:[{seq:1,kind:'started'|'stdout'|'stderr'|'finished'|'uncertain',text:'',status:'succeeded'|'failed'|'cancelled'|'timed_out',exit_code:null|int}]}`。seq 从 1 连续递增。每批最多 100 个；单事件文本最多 16000 字符。finished 才使用 status/exit_code。返回 `{ack_seq, stop_requested}`。相同序号与内容的重复提交返回原 ack，内容冲突或序号缺口返回 409。结束事件是唯一释放宿主机占用的条件。

`POST /api/agent/runs/{id}/artifacts/{filename}` 上传原始字节，签名包含原始内容；文件名仅 basename，最大 16 MiB，同名同内容幂等，同名不同内容冲突。先上传产物再发送 finished，下载必须管理员会话认证。Agent 不上传符号链接和运行目录外文件。

## 管理接口与页面

- `GET /api/hosts` → Host 数组。
- `GET /api/hosts/agent-download` → 仅含固定源码清单的 Agent ZIP（管理员下载，不包含机器身份或本地数据）。
- `POST /api/hosts/enrollment-codes` → `{code,expires_at,server_url}`。
- `POST /api/hosts/{id}/approve`、`reject`、`revoke` → Host。
- `PATCH /api/hosts/{id}/mode`，`{dispatch_enabled:bool}` → Host。
- Host：`id,name,machine_id,approval_status(pending/approved/rejected/revoked),dispatch_enabled,agent_dispatch_enabled,interactive_session,online,state(pending/non_dispatch/idle/running/stopping/offline/uncertain/rejected/revoked),last_seen_at,agent_version,active_run_id,created_at`。
- `POST /api/hosts/{id}/runs`，`{task_id:int,request_id:UUID-string}` → Run。请求 ID 重复必须返回同一运行；更换目标/任务时冲突。
- `GET /api/remote-runs` → 最近 100 个 Run；`GET /api/remote-runs/{id}` → Run 含最近 500 条 `events`、`events_truncated` 和 `artifacts`。
- `POST /api/remote-runs/{id}/stop` → Run。queued 直接 cancelled；已领取则 stopping，等待执行端确认。
- Run：`id,host_id,host_name,task_id,task_name,package_hash,status(queued/preparing/running/stopping/uncertain/succeeded/failed/cancelled/timed_out),created_at,started_at,finished_at,exit_code,error,ack_seq,stop_requested,waiting_reason`。
- Artifact：`name,size,sha256,download_url`。

后台管理写操作校验管理员会话和同源 Origin/Referer，并写现有审计表。机器 API 只接受签名身份，不接受用户 Cookie 作为机器认证。主控不得向其他机器返回未指派任务的源码和日志。

## 业务环境事实（Agent 0.4.0 增量）

协议仍为 v1，环境事实能力从 0.4.0 提供，当前 Agent 为 0.5.0，最低兼容 0.3.0。旧 Agent 沿用旧运行/回传，主控不把没有明细的记录显示为已校验。新 Agent 仅在 claim 响应含 `environment_report_version:1` 时准备业务；缺少该能力时结束为失败并提示先升级主控，不下载或执行业务脚本。升级入口见下文。

0.4.0 首次 claim 时，主控在原领取事务将本次运行标记为 `environment_report_required=1`。必须先收到合格的环境记录，才接受该运行的 started；没有业务开始事实不能提交 succeeded。迁移不把已领取的旧运行改为新协议要求。

环境通过原签名事件回传：`{seq,kind:'environment',text:'',environment:{report_version:1,package_hash,requirements_hash,cache_reused,status:'ready'|'failed',stage,python:null|string,bits:null|32|64,packages:[{name,version}],pip_check,interpreter_verified,requirements_verified,verified_at,error}}`。stage 为 create/install/interpreter/pip_check/requirements/ready，verified_at 必须带时区；error 最多 1000 字符，最多 1000 个包，整份 environment 不超过 256 KiB。事件批次仍用原 2 MiB/100 条限制。仅 environment 事件可以带该字段，未知字段拒绝。

Agent 验证本次独立环境路径、Python 3.12/64 位，执行 pip check，再核对真实包名/版本满足冻结清单。缓存命中也重验；失败清除 ready 标志、不执行业务，下一次新运行可重建。目标探测只读安装元数据，不 import 业务代码或第三方包。环境路径仅在 Agent 内核对，结构化事件不上传本地解释器/目录。

主控根据原冻结 remote run 核对包哈希、清单哈希、检查结果及直接依赖约束；host_id、代码版本、目标电脑均取原运行，不能由 Agent 改写。环境仅在 claim 后、业务开始前写入；`remote_environment_records` 按 run 唯一，事实、原事件及 ack 同事务。相同序号和内容重放幂等，不允许新序号覆盖记录。兼容旧序号重放时，不向旧事件规范化内容添加 `environment:null`。

`GET /api/remote-runs` 增加 environment_status（pending/unreported/ready/failed）；详情增加 environment 与主控 received_at，原版本/目标字段保持。`GET /api/apps/{id}/dependency-details` 的 remote_environments 按运行返回最近十次，和主控 environments 分开。两处均需管理员身份，默认折叠展示。environment_status 为历史事实或待回传，不证明当前电脑始终可用；签名不是硬件环境证明。

详细代码、兼容和现场边界见[阶段 C 逐项验收](Agent业务环境阶段C精细对齐与验收_2026-10-16.md)。

## 本机升级暂停（Agent 0.4.1 增量）

`POST /api/agent/upgrade-pause` 和 `/api/agent/upgrade-resume` 沿用机器签名，JSON 只有 `token`（32 位十六进制），请求上限 4096 字节。接口从签名确定本机身份，不接受指定其他宿主机，也不使用管理员 Cookie。

暂停仅允许已申请/批准且没有活动租约的宿主机，在原事务中写入 token 和 30 分钟有效期；心跳候选及 claim 同时核对，防止升级检查后再领取。相同 token 可重试，不同有效 token 拒绝；恢复只接受原 token，重复恢复成功。公开状态只返回 `upgrade_pending` 和“升级中”，不返回 token。

本机升级复用 `manage.ps1`，串联校验、停派发、journal 核对、停止、备份、替换和启动，保留主控/本机调度开关。失败尝试恢复旧程序，不回退 journal；断网时 `upgrade.resume.json` 保留 token/版本供重连恢复。此为 0.4.1 加入的本机流程；0.5.0 的远程升级占用不能由该恢复接口释放，需按下节确认结果和重连。原命令入口保留，[本机阶段记录](Agent一键升级需求与验收_2026-10-16.md)保持其历史时点。

## 主控发起升级（Agent 0.5.0 增量）

先更新主控，再用新包本机引导旧 Agent 一次。包内含 `controller-trust.json` 公钥，Agent 保存到数据目录的 `controller-update-key.json`，不自动替换。主控私钥位于数据目录 `agent-updates/controller.key.dpapi`，沿用当前 Windows 用户 DPAPI。

- `POST /api/hosts/{host_id}/upgrade`：仅超级管理员及同源 JSON；`request_id` 为 32 位 hex。请求、冻结包、机器占用和审计同事务，重复编号返回同一记录，不能改目标。
- 心跳新增可选 `remote_upgrade_ready`，默认 false；0.5.0 且可信配置就绪才能远程升级。响应 `update` 为 null 或 `{descriptor,signature}`。descriptor 包含 `format:1,id,host_id,machine_id,from_version,target_version,token,sha256,size,expires_at`，按 UTF-8、键排序、无多余空格、非 ASCII 不转义生成 JSON 后使用 Ed25519 签名，signature 为 Base64。
- `GET /api/agent/updates/{id}/package`：原机器签名认证，只返回自身已批准且有效的固定 ZIP，不接受外部 URL；下载后核对签名描述中的大小与 SHA-256，再核对包内清单/版本/路径。
- `POST /api/agent/updates/{id}/report`：原签名认证，上限 4096 字节，字段为 `token,phase,success,agent_version,message`。phase 为 installing 或 result，message 最多 1000 字符；token/机器/原版本/状态必须匹配，结果相同可重试，不可改写。

状态为 queued → downloading → installing → awaiting_reconnect → succeeded/failed。结果回传后仍占用，匹配版本的新心跳才能完成并释放派发。30 分钟内未领取可 expired 并释放；已下载/安装但无明确结果转 uncertain 并继续占用。晚到旧领取可取消尚未下载的升级，保持原运行。主控公开 `agent_update` 只含状态、版本、消息和时间，不返回 token、冻结 ZIP 或签名私钥。

Agent 使用 `remote-update.json` 持久回执，下载线程不阻断心跳；后台 PowerShell 调用原升级流程并延后释放。成功启动或恢复旧版后，从本地完成记录补传结果；已交付的安装器不因重启而重复启动。完整验收见[主控远程升级需求与验收](主控远程升级需求与验收_2026-10-16.md)。

## 版本与运维边界

Run 内容哈希是冻结执行快照，任务同时绑定不可变 `code_version_id` 与 `version_sequence`。Agent 0.3.0 提供人工启动、停止、日志、升级和备份恢复入口；程序升级与机器身份/本地 journal 分开，恢复不能绕过 DPAPI、运行租约或状态待确认。两台真实 Windows 验收仍需单独记录，源码和隔离测试不等于现场通过。
