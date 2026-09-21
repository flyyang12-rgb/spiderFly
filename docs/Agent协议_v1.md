# Agent 协议 v1（首个实施增量）

本协议用于局域网宿主机接入和指定机器手动执行。主控沿用 SQLite，Agent 使用独立本地 SQLite 日志；只有主控访问中心数据库。现有单机队列暂保留，远程运行不进入旧本机执行或自动修复队列。完成迁移前，网页分别显示本机与远程运行。

## 身份与通信

Agent 生成 Ed25519 密钥对，私钥仅存本机（Windows DPAPI）。管理员通过会话认证生成接入码，20 分钟有效；接入码在首次成功申请时原子消费，避免两台机器同时申请。重复申请相同机器 ID 和公钥返回原申请，不能绑定其他公钥。管理员批准后，机器 ID 与公钥构成长期身份；撤销后所有机器接口拒绝访问。

使用可配置 `http://<局域网主控>:9356` 或 HTTPS 地址；v1 使用 HTTP 轮询。签名验证身份与请求完整性，HTTP 不加密内容，因此仅用于可信局域网。HTTPS 使用同一协议。Agent 不跟随跨地址重定向。

注册：`POST /api/agent/register`，JSON 为 `enrollment_code, machine_id, name, public_key, agent_version, protocol_version`。公钥为 32 字节 Ed25519 公钥的 Base64。返回 `{host_id, approval_status, protocol_version:1, minimum_agent_version, current_agent_version}`。

协议号和 Agent 程序版本分别校验。协议号不同，或 Agent 低于主控公布的最低版本时，注册/心跳返回 409，且不会消费接入码、更新宿主机状态或下发任务。当前协议为 v1，最低 Agent 为 0.3.0。主控只提示从宿主机页面下载并人工升级，不向 Agent 推送程序，也不允许旧 Agent 盲目自更新。

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

## 版本与运维边界

Run 内容哈希是冻结执行快照，任务同时绑定不可变 `code_version_id` 与 `version_sequence`。Agent 0.3.0 提供人工启动、停止、日志、升级和备份恢复入口；程序升级与机器身份/本地 journal 分开，恢复不能绕过 DPAPI、运行租约或状态待确认。两台真实 Windows 验收仍需单独记录，源码和隔离测试不等于现场通过。
