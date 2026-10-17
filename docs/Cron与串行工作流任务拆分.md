# SpiderFly Cron 与串行工作流任务拆分

版本：v1.1。基线日期：2026-10-13。2026-10-14 状态：T01—T12 实现及相应隔离检查完成，T13 的 M3 页面、构建和交付部分完成，M1—M3 已由正式主控加载。T14 的空闲检查、完整备份和正式重启已执行，真实业务、WSL、桌面、Agent 及恢复/回退现场剩余项仍待验收；未提交或推送。

需求基线：[Cron 与串行工作流需求](Cron与串行工作流需求.md)。阅读证据：[场景边界](Cron与串行工作流场景边界_2026-10-13.md)。需求编号 R01—R18、验收编号 AC01—AC24 均引用需求文档。

2026-10-14 新确认的任务列表、创建入口和导航统一另见 [总体需求](SpiderFly总体需求.md) U01—U08 及 [本轮对齐报告](统一任务中心参考实现与需求对齐报告_2026-10-14.md)。本文 T01—T14 保留 Cron/执行及现场交付任务的原编号；其完成状态不代表新增 UA01—UA12 已完成，也不把新界面工作改称原 M4。

本次同步用户确认的简要流程历史保留与新增 Cron 维护期间跳过计划规则及后续实施状态。审查证据及剩余范围见[精细对齐报告](Cron与串行工作流精细对齐报告_2026-10-13.md)，实施、隔离检查、正式加载与未完成验收见[M1 记录](M1实现与验证记录_2026-10-13.md)、[M2 记录](M2实现与验证记录_2026-10-13.md)和[M3 记录](M3实现与验证记录_2026-10-14.md)。

主改 SpiderFly，首版在 Windows 主控本机执行，脚本自行处理业务目录和数据库。继续使用 SQLite、现有本机队列和结果判定；不修改 RPAC-main，不引入 Redis、PostgreSQL、独立 Worker 或同机 Agent。

## 1. 实施顺序与阶段出口

| 阶段 | 任务 | 阶段交付 | 阶段出口 |
| --- | --- | --- | --- |
| M1：单任务 Cron | T01—T04 | 表达式、预览、可靠触发、维护轮次协调、任务编辑和时间表 | 本机单任务可按 Cron 运行，登记失败可恢复；AC01—AC08 的单任务部分、AC19 新 Cron 维护部分及相关兼容回归通过 |
| M2：手动串行流程 | T05—T11 | 定义、快照、维护隔离、本机执行、成功推进、停止恢复和页面 | 非业务 A→B→C 正确运行，失败/停止不放行，版本变化不影响本轮；AC09—AC21 的手动流程部分通过，AC16 完整双向计划行为留给 M3 |
| M3：整流程计划 | T12 | 每日、每周、Cron 统一启动整轮 | 计划冲突、重叠、漏触发、时间表关联完整；AC04—AC08、AC16—AC17、AC22 通过 |
| M4：回归与现场交付 | T13—T14 | 全套回归、前端构建、现场检查和发布资料 | AC01—AC24 有对应结果；未验证条件如实记录，不写为通过 |

M1、M2 可以分别验收，不要求一次上线全部功能。每阶段只在达到出口后记录完成；阶段进入现场发布仍需包含 T13 的相应回归和 T14 的发布检查。

下图表示依赖关系，不表示所有阶段完成；当前进度见本文末尾。

```mermaid
flowchart LR
    A[T01 数据与基线] --> B[T02 Cron 解析]
    B --> C[T03 持久触发]
    C --> D[T04 单任务页面]
    D --> E[T05 流程模型]
    E --> F[T06 整轮快照]
    F --> G[T07 维护版本隔离]
    G --> H[T08 本机队列接入]
    H --> I[T09 结果协调恢复]
    I --> J[T10 停止竞争]
    J --> K[T11 手动流程页面]
    K --> L[T12 整流程计划]
    L --> M[T13 回归构建]
    M --> N[T14 现场交付]
```

## 2. 数据与模块约定

以下为完整结构。M1 已接入 cron_rounds/execution_settlements，M2 已接入 workflows/workflow_members/workflow_runs/workflow_steps；M3 接入 workflow_triggers、整流程计划与双向独立计划冲突。2026-10-14 M1—M3 已加载，约束不因分阶段交付而省略。

| 数据对象 | 最少保存的内容 | 必须具备的约束 |
| --- | --- | --- |
| 计划触发记录 | 对象类型/ID、计划配置摘要、scheduled_for、登记/入队时间、决定、合并范围、错误、原执行及维护/至多一次修复重跑关联 | 对象类型/ID/计划时间唯一；待入队及维护恢复指向原轮次，不跨触发复用 |
| 流程定义与有序成员 | 名称、说明、顺序、task_id、统一触发方式、schedule_enabled、next_run_at、归档与操作人 | 非空、有序、任务不重复；独立计划冲突双向检查 |
| 流程运行 | 定义引用、发起人、来源、request_id、计划/实际时间、状态、停止标志、起止时间、原因 | 同一流程最多一轮未结束；请求判重；终态不可回到活动态 |
| 本轮步骤快照与历史摘要 | 位置、名称、任务/版本编号、时间、状态、错误摘要；运行期间另存源码/依赖、解释器/策略、模板内容及参数 | 本轮位置唯一；摘要脱离可删除任务/执行记录；文件受所有未结束轮次保护，删除后关闭失效入口 |
| 原执行的新增关联与收尾事实 | 触发记录或步骤关联、冻结运行参数、收尾确认 | 普通历史记录允许无新增关联；流程 pending 不被版本发布重绑 |

流程运行建议状态为 waiting、running、stopping、success、failed、cancelled；步骤建议状态为 waiting、pending、running、stopping、success、failed、timeout、cancelled、skipped。原 executions 保持现有 pending/running/success/failed/timeout/cancelled，不把新增流程状态塞入原执行状态字段。

## 3. M1：单任务 Cron

### T01：建立隔离基线与触发数据迁移

需求：R01、R04、R17、R18。依赖：无。

- 按[开发与验证](开发与验证.md)准备隔离副本，断言数据路径指向副本，保留当前单任务、计划、版本与维护回归结果。
- 为新增触发建立 SQLite 持久记录、唯一约束及查询方式；补充运行关联和收尾事实的可迁移存储。不清理或重写历史执行，不自动转换旧计划。
- 保存新 Cron 原轮次、原执行、维护及至多一次修复重跑的持久关联和唯一约束；原触发身份不改指其他轮次，旧记录不回填成新维护轮次。
- 迁移可重复运行；旧记录新增字段使用兼容默认值。明确启动恢复和健康检查的接入顺序。

代码落点：[database.py](../backend/app/database.py)、[runtime.py](../backend/app/services/runtime.py)、[cron_runs.py](../backend/app/services/cron_runs.py)、[native_process.py](../backend/app/native_process.py)、[test_database_migrations.py](../backend/tests/test_database_migrations.py)。当前新增 cron_rounds 和 execution_settlements；源码已接入，最终迁移回归待记录汇总。

完成条件：新建库与旧版合成库升级均通过；重复迁移不产生重复数据；未改变旧 manual/daily/weekly 行为。交付迁移说明与隔离验证记录。

### T02：统一 Cron 解析、计算与预览

需求：R02、R03。依赖：T01。

- 在 scheduling 的统一入口接入五字段 Cron；选择一套解析库或实现，并封装星期转换、北京时间与边界校验。需要新依赖时同步依赖清单，不并用第二套预览解析器。
- 提供校验和未来 5 次计算；明确基准时刻、搜索上限、无未来日期错误及规范化结果。
- 复用 daily/weekly 现有契约；只新增 cron，不借此次开放 once/interval。

代码落点：[cron_schedule.py](../backend/app/cron_schedule.py)、[scheduling.py](../backend/app/scheduling.py)、[schemas.py](../backend/app/schemas.py)、[schedules.py](../backend/app/api/schedules.py)。当前使用纯标准库解析，无新增依赖；预览 API 已接入，完整验收状态见日期记录。

完成条件：AC01—AC03 通过；覆盖星期 0/7 与名称、月底、闰日、非法字段和有界拒绝；预览与 next_run_at 使用同一计算结果。

### T03：可靠触发、入队判重与漏触发恢复

需求：R04—R07、R11、R15、R17。依赖：T01、T02。

- 新 Cron 分支在事务中登记触发并推进计划；同事务入队或通过持久待入队记录恢复，禁止继续使用吞异常逻辑。
- 在事务中检查对象与任务占用，待入队触发也计入未结束轮次；相同计划点或请求重试返回原运行，手动与计划竞争不重复创建执行。恢复派发前重新检查停用、删除和目标限制。
- 接入实际收尾确认；排队、运行、停止或收尾待确认均占用本轮。新增 Cron 最多一轮，跳过、错误和停机合并可查询。
- 在原失败写入至维护决定落库之间持续占用原轮次；生成、等待验证、验证及一次重跑期间新计划登记跳过，不积压或事后补跑。维护发布、重跑入队和关联原子提交，不复用其他触发/请求的 pending；先恢复维护及重跑关联并核对可信收尾，再处理到期点。维护未进入、无法修复、取消或中断均保存决定与原因，不重复发起模型或第二次重跑。
- 主控恢复只补最近一个错过时间点；主动关闭后重新开启不追补关闭期间。旧 daily/weekly 保持原策略和目标路由。
- 新 Cron 维护占用期间的漏点，恢复时登记或合并为维护跳过；释放轮次与推进计划须可恢复，不能因先结算维护而把占用期间漏点补跑。占用结束后才接受后续计划点。

代码落点：[scheduling.py](../backend/app/scheduling.py)、[execution_queue.py](../backend/app/services/execution_queue.py)、[runtime.py](../backend/app/services/runtime.py)、[runner.py](../backend/app/runner.py)、[tasks.py](../backend/app/api/tasks.py)、[maintenance.py](../backend/app/maintenance.py)。Cron 维护协调属于 M1，不等待 M2/T07。

完成条件：AC04—AC08 的单任务部分及 AC19 的新 Cron 部分通过；在“登记后未入队、入队后关联未返回、失败后维护决定未落库、修复重跑已终态但尚未收尾”等边界模拟中断，恢复不新增业务尝试。维护各阶段到期均跳过，恢复先结算维护也不补占用期间漏点；恢复原关联不产生修复循环。既有 Agent 路由无回归，远程目标新增 Cron 明确拒绝。

### T04：单任务 Cron 页面与时间表

需求：R03、R07、R16—R18。依赖：T02、T03。

- 更新公共 schema、任务创建/编辑及触发常量，加入预设、表达式、北京时间、错误说明和未来 5 次预览。
- 时间表从后端统一计算的计划点和触发事实读取 Cron 数据；同一天多次运行按 scheduled_for 关联，不用当天最后一次执行覆盖全部计划。
- 显示重叠跳过、合并漏触发、入队错误和运行链接；请求失败保留表单，不虚报保存成功。
- 触发详情展示原失败、维护状态和一次修复重跑的分别关联；维护期间跳过原因可见，修复成功不覆盖原失败记录。

代码落点：[schedules.py](../backend/app/api/schedules.py)、[task_queries.py](../backend/app/services/task_queries.py)、[TaskEditor.vue](../frontend/src/features/tasks/TaskEditor.vue)、[useTasks.js](../frontend/src/features/tasks/useTasks.js)、[CreateTaskPage.vue](../frontend/src/features/apps/CreateTaskPage.vue)、[CronEditor.vue](../frontend/src/features/schedule/CronEditor.vue)、[CronEntryDetails.vue](../frontend/src/features/schedule/CronEntryDetails.vue)、[constants.js](../frontend/src/lib/constants.js)、[useSchedule.js](../frontend/src/features/schedule/useSchedule.js)、[SchedulePage.vue](../frontend/src/features/schedule/SchedulePage.vue)。

完成条件：AC01、AC08、AC21—AC23 的单任务部分通过；隔离前端构建成功并同步 dist；完成创建、编辑、拒绝非法表达式和多计划点显示的页面检查。M1 可验收。

## 4. M2：手动本机串行流程

### T05：流程定义、运行模型与权限

需求：R05、R08、R09、R17。依赖：T01—T04。

- 建立流程、有序成员、运行和本轮步骤模型及增量迁移，保存单步骤流程，禁止空列表与重复任务。
- 提供定义列表、创建、编辑、归档及运行查询；先支持手动启动。启动使用 request_id 判重，检查任务启用、环境就绪、本机目标和执行策略。
- 新入口执行管理员操作权限，读记录和下载沿现有鉴权；历史保留冻结名称与版本。归档关闭未来计划，活动运行或待入队触发阻止归档；计划冲突校验抽成统一规则供 T12 复用。
- 独立保存步骤名称、版本号、顺序、时间、状态和错误摘要，成员删除不级联抹掉简要历史。归档不等于删除任务资料；含已删除成员的定义拒绝新启动或整轮重跑，不自动省略或复活步骤。

代码落点：[database.py](../backend/app/database.py)、[schemas.py](../backend/app/schemas.py)、[security.py](../backend/app/security.py)、[main.py](../backend/app/main.py)、[工作流 API](../backend/app/api/workflows.py)、[流程服务](../backend/app/services/workflows.py)。模型、请求身份、权限与修订检查已接入；AC16 的双向计划行为由 T12/M3 接通，证据见 M3 记录。

完成条件：AC09、AC16、AC18、AC21 的模型与权限部分通过；不同流程的步骤或运行不能互相覆盖，归档活动流程被拒绝，重复请求返回原记录。

### T06：冻结整轮源码、模板、环境与参数

需求：R10、R11、R17。依赖：T05。

- 在启动时准备全部步骤不可变快照，覆盖源码/版本/依赖、环境引用、模板内容与哈希、通知、本轮全局超时和浏览器端口。不可只保存维护快照或当前文件路径。
- 校验快照文件后事务提交本轮及引用；准备失败不留下可运行的半轮。保护活动快照引用的环境和文件，清理不能使后续节点失效。
- 删除清理核对所有未结束轮次引用；数据库已写失败或单步结束不解除保护。简要历史先持久保留，确认相关轮次收尾后才清理被删除任务的源码/模板内容、日志、平台产物及无活动引用环境。
- 为 runner 增加读取冻结参数的适配，保留原独立任务路径；不为了方便固定模板而增加步骤间文件接力。

代码落点：[runner.py](../backend/app/runner.py)、[environments.py](../backend/app/environments.py) 的 delete_task_bundle、remove_managed_app_storage、cleanup_old_environments，及[流程快照服务](../backend/app/services/workflows.py)；快照独立于 AI 配置，已接入整轮文件与解释器引用保护。

完成条件：AC14—AC15 通过；无 AI 配置仍能运行；修改源码、模板、通知或版本不影响本轮；缺失快照明确失败，不回退到当前版本。

### T07：隔离流程与旧自动维护、版本重绑

需求：R10、R15、R17。依赖：T05、T06。

- 根据明确的步骤关联识别流程执行，排除 maintenance 和手工版本发布对这些 pending 的脚本、解释器和维护快照重绑。
- 排除流程 pending 被选为“最早兼容排队记录”复用为修复重跑；流程失败不进入旧自动启用与重跑链路。
- 普通独立任务的维护、发布、pending 衔接与一次重跑保持原行为；单独修复成功不改变旧流程终态。
- 新 Cron 已在 T03 关联维护轮次；本项回归保护其 pending 不被旧兼容复用跨轮挪用，同时保留普通历史 manual/daily/weekly 的既有衔接行为。

代码落点：[maintenance.py](../backend/app/maintenance.py)、[task_versions.py](../backend/app/task_versions.py)、[execution_queue.py](../backend/app/services/execution_queue.py)。

完成条件：AC14、AC19 通过；分别覆盖手工发布和维护发布两条改写入口，同时验证普通 pending 仍可按旧规则衔接。此项完成前不开放流程执行入口。

### T08：当前步骤接入本机串行队列

需求：R05、R10、R11。依赖：T05—T07。

- 按步骤快照入队，只创建当前步骤的真实 execution；事务中完成任务占用检查、执行创建与步骤关联。无需创建未来步骤的执行记录。
- 与独立手动执行及其他流程争用同任务时等待或按既有规则拒绝，防止检查后插入的竞争。安全停用/删除阻止尚未开始的流程步骤。
- 删除关联 pending 或未来成员时先同步摘要、取消及恢复事实，不能先硬删再让协调器丢失执行关联；不从历史源码重建已删除成员。
- 复用本机资源检查、模板准备、停止控制和串行 worker，不新增生产 worker，不绕过机器锁或接管用户资源。

代码落点：[execution_queue.py](../backend/app/services/execution_queue.py)、[runner.py](../backend/app/runner.py)、[流程协调服务](../backend/app/services/workflows.py)、[native_process.py](../backend/app/native_process.py)。当前步骤事务入队及 native 可信收尾已接入。

完成条件：AC09、AC17—AC18 通过；同任务不重叠，等待下游不占机器，独立任务仍能在步骤之间进入队列；原资料存在时可链接日志和产物，删除后摘要仍可读且无失效入口。

### T09：最终结果协调与重启恢复

需求：R12、R14。依赖：T03、T08。

- 在 run_execution 完整返回及 worker 兜底失败写入后协调；只根据关联 execution_id 的最终业务结果和收尾事实推进，不能读 tasks.last_status。
- 成功后事务推进并关联下一步；重复调用只处理一次。failed/timeout 跳过剩余步骤，cancelled 取消剩余步骤；终态不可回退。
- 在启动和串行边界恢复 waiting、已有 pending、成功但未推进、意外中断 running 与快照错误。没有收尾确认先核对资源；不新建“恢复重跑”，不借维护结算器代替流程恢复。

代码落点：[流程协调服务](../backend/app/services/workflows.py)、[execution_queue.py](../backend/app/services/execution_queue.py)、[runtime.py](../backend/app/services/runtime.py)、[database.py](../backend/app/database.py)、[execution_results.py](../backend/app/execution_results.py)。结果解析契约沿用现有实现，正常完成、兜底失败及启动恢复已接入协调。

完成条件：AC09—AC10、AC13、AC15、AC17 通过；failure/manual_required 且退出码 0 不推进；模拟成功保存后崩溃和重复协调，下一步不重复创建。

### T10：停止、取消与竞争收尾

需求：R07、R12—R14。依赖：T08、T09。

- 停止请求先保存停止标志，再取消 waiting/pending 或向 running 请求停止。所有派发入口读取标志；完整收尾前保持停止中。
- 将现有执行取消/停止接口产生的终态接回流程协调，避免从单执行入口绕过流程。
- 覆盖停止与成功回写、重复停止、主控重启、任务停用/删除等竞争；只作用于本次进程树，不以进程名全局结束桌面软件。
- 删除先与流程取消/停止协调；未确认收尾时不清理活动快照或环境。只清平台受管资料，不删除或回滚业务目录及外部数据库数据。

代码落点：[工作流 API](../backend/app/api/workflows.py)、[流程协调服务](../backend/app/services/workflows.py)、[executions.py](../backend/app/api/executions.py)、[runner.py](../backend/app/runner.py)、[native_process.py](../backend/app/native_process.py)。持久停止与业务放行前复核已接入。

完成条件：AC08、AC11—AC13、AC18 通过；在下一步入队边界请求停止时下游不启动，停止标志跨重启保留，任务停用不误报当前进程已停止。

### T11：工作流编辑、详情与手动重跑页面

需求：R08、R13、R16、R18。依赖：T05—T10。

- 新增有序列表式流程编辑、成员校验说明和手动启动；不增加 DAG 画布或步骤间文件配置。
- 显示当前轮次、当前步骤、冻结版本、时间、等待/失败/跳过原因；复用运行日志、结果和产物入口。
- 成员删除后仍显示六类简要历史字段，资料已删除时关闭源码/模板、日志与产物入口；历史查询不依赖已删除任务或 execution 行。
- 支持流程停止、终态整轮重跑与历史后端分页；重跑生成新请求和新运行，不修改旧轮。保留计划入口给 T12 接通，未接通时不显示为可用功能。

代码落点：[WorkflowsPage.vue](../frontend/src/features/workflows/WorkflowsPage.vue)、[WorkflowEditor.vue](../frontend/src/features/workflows/WorkflowEditor.vue)、[WorkflowRunDetail.vue](../frontend/src/features/workflows/WorkflowRunDetail.vue)、[useWorkflows.js](../frontend/src/features/workflows/useWorkflows.js)、[useWorkspace.js](../frontend/src/workspace/useWorkspace.js)、[useWorkspaceData.js](../frontend/src/workspace/useWorkspaceData.js)、[App.vue](../frontend/src/App.vue)。手动流程与简要历史页面已接入，统一计划入口由 T12/M3 接通并验收。

完成条件：AC09—AC21 的页面路径通过；管理员和普通成员可见/可操作边界正确，刷新后保留服务器状态；前端构建与页面检查通过。M2 可验收。

## 5. M3：整流程统一计划

### T12：流程每日、每周与 Cron

2026-10-14 实现、隔离回归、实际 Codex 页面及到点运行验收完成；正式主控空闲备份后已同步 dist 并重启加载。M3 阶段检查完成，T14 的真实业务和现场剩余项另列；证据见[M3 记录](M3实现与验证记录_2026-10-14.md)。

需求：R03—R09、R16。依赖：T03、T05—T11。

- 复用同一计算和触发登记服务，在计划点创建整轮流程，而非只运行首个任务。每日/每周沿用原时间格式，但新增流程统一使用最多一轮与漏触发规则。
- 接通 schedule_enabled 与双向成员计划冲突检查；关闭流程计划不停止本轮、不关闭成员。启用从当前时刻计算未来时间，表达式修改不改已登记记录。
- 时间表和触发查询区分 task/workflow，对整轮保存请求、scheduled_for、合并范围、跳过与错误关联；界面预览与实际调度共用计算。
- 按用户确认统一定时弹窗，单个任务或整个工作流可选；上传与任务管理保留，工作流页与时间表共用 PlanEditor。

代码落点：[scheduling.py](../backend/app/scheduling.py)、[runtime.py](../backend/app/services/runtime.py)、工作流 API/服务、计划查询 API、工作流编辑页与 [SchedulePage.vue](../frontend/src/features/schedule/SchedulePage.vue)。

完成条件：AC01、AC04—AC08、AC16—AC17、AC22 通过；有明确证据证明 Cron 启动 A→B→C 整轮，不只是 A；M3 可验收。

## 6. M4：回归、现场验收与交付

### T13：完整回归、构建与文档同步

需求：R17、R18。依赖：T01—T12；每阶段先执行相关子集。

- 全部代码测试在隔离副本运行，清除项目环境变量并断言数据路径；采用合成脚本、临时目录和模拟通知，不导入正式 app 或访问业务数据库。
- 对照 AC01—AC23 检查计划、状态、权限、快照、竞争、恢复和普通任务维护。相关旧测试入口包括 scheduling、scheduled_queue、runner、force_stop、execution_results、task_versions、maintenance、hosts；新增用例按模块组织。
- 前端构建后同步 dist 并完成页面检查；更新功能地图、文档索引、代码对照和 CONTEXT，分别记录已实施、测试、服务加载和现场结果。

完成条件：受影响回归无未解释失败，跳过项明确原因；每个 AC 有结果或待验证说明；迁移和故障恢复边界有测试证据，git diff --check 通过，未混入运行资料。

### T14：实际主控验收与发布准备

需求：R11、R17、R18。依赖：对应阶段的代码及 T13 检查通过。

- 在实际 Windows 主控用测试身份、独立验证目录和非业务脚本，核对排队、资源占用、停止、重启和计划；桌面脚本补锁屏/会话测试。使用生产机器锁，不开同机 Agent 绕过限制。
- 在正式升级前确认空闲并备份，验证数据库迁移、停机漏触发行为、恢复和回退步骤。回退先关闭新增计划并处理活动轮次，不能让旧版本解释未知 Cron；恢复备份后的计划和运行关联需重新核对。业务目录/数据库的写入不由平台回滚。
- 记录源码提交、隔离测试、前端产物、服务实际版本和现场结果。只有对应阶段全部条件满足才发布该阶段能力；本任务文档不表示已获正式部署或推送授权。

完成条件：AC24 通过，发布包与源码/数据库迁移配套，备份可恢复，CONTEXT 准确。若现场未执行，阶段状态保留待验收，不写成已上线。

## 7. 需求到任务的对应

| 需求 | 主任务 |
| --- | --- |
| R01 范围与执行架构 | T01、T08 |
| R02 Cron 契约 | T02 |
| R03 预览与时间表 | T02、T04、T12 |
| R04 可靠触发 | T01、T03、T12 |
| R05 判重与重叠 | T03、T05、T08、T12 |
| R06 漏触发 | T03、T12 |
| R07 开关与编辑 | T03、T04、T10、T12 |
| R08 定义与权限 | T05、T11 |
| R09 计划冲突 | T05、T12 |
| R10 整轮快照 | T06、T07、T08 |
| R11 本机执行与资源 | T03、T06、T08、T14 |
| R12 结果推进 | T09、T10 |
| R13 停止与重跑 | T10、T11 |
| R14 流程恢复 | T09、T10 |
| R15 维护兼容与流程隔离 | T03、T07 |
| R16 页面与审计 | T04、T05、T11、T12 |
| R17 兼容 | T01、T03—T08、T13、T14 |
| R18 验收与交付 | T04、T11、T13、T14 |

开发任务不按估算工时判定完成。完成一项时，应在本节后追加任务编号、代码提交或工作区状态、隔离检查、页面检查和现场结果；不存在的证据标为待完成。

## 8. 阶段进度（2026-10-14 更新）

| 任务 | 实施状态 | 验证与剩余事项 |
| --- | --- | --- |
| T01 | 新 Cron 轮次、请求/计划唯一约束、execution_settlements 收尾表与启动恢复源码已实施 | 隔离迁移和完整兼容回归通过；正式主控已加载增量，现场剩余项单列 |
| T02 | 纯标准库五字段解析、next_run、最近漏点及未来 5 次 API 已实施 | 纯解析 18 项、相关 API、非法输入和真实隔离页面预览通过 |
| T03 | 新 Cron 持久触发/入队、占用/跳过、维护关联/恢复及 native/readonly-v1 收尾源码已实施 | 轮次 16、集成 14、native_process 21、runner 7 项通过；真实隔离原生运行与结算通过；真实 WSL 维护/回执待验收 |
| T04 | 编辑、上传、异步预览、今日/本周后端 Cron 时间表、原因和运行关联已实施 | mock、隔离构建、dist 同步、真实隔离页面保存与同日多点展示通过 |
| T05 | 流程定义/有序成员/独立轮次/步骤、管理员操作、请求判重及定义修订已实施 | 模型/API 隔离回归完成；双向计划冲突由 T12 接通并通过相关隔离及实际页面检查 |
| T06 | 整轮源码、依赖、环境、模板、通知、超时及浏览器端口冻结，全部未结束引用保护和删除简要摘要已实施 | 快照失败、无 AI、未来步骤环境及删除收尾边界隔离检查完成 |
| T07 | 流程失败不进入旧维护，人工/维护发布不重绑流程 pending，维护不复用流程执行已实施 | 普通独立任务旧维护和 pending 衔接兼容回归完成 |
| T08 | 当前步骤事务占用检查、唯一真实 execution 关联及原 native 队列接入已实施 | 同任务竞争、后续等待不预占、真实隔离串行脚本验证完成 |
| T09 | 最终业务结果及三项可信收尾协调、兜底失败/启动恢复/重复事件保护已实施 | 成功推进、failure/manual_required/快照损坏停止下游及恢复边界隔离检查完成 |
| T10 | 持久停止、launcher 放行前复核、关联取消/停止及删除协调已实施 | 停止竞争与可信收尾隔离回归完成；真实页面 S→A 停止后 S 取消且 A 无执行已确认 |
| T11 | 流程排序编辑、详情/摘要/历史分页/停止/整轮当前版本重跑页面已实施 | 真实隔离页面成功、失败不放行、停止及 390/320 像素窄屏编辑/详情验证完成，表格仅内部滚动 |
| T12 | 每日/每周/Cron、独立开关、双向成员冲突、原子登记整轮及统一定时弹窗已实现 | 后端、Vue 和实际 Codex 页面检查通过；真实到点 Cron 整轮、重叠跳过、关闭计划保留本轮及每日失败中断已验证，dist 与正式加载完成 |
| T13 | M1—M3 范围隔离回归、前端构建、实际隔离页面和相应文档交付完成 | 后端 588 项中 570 通过、18 条件跳过；Vue 21 项、86 模块构建通过，静态资源同步核对；不据子场景检查宣称全部 AC 或业务现场通过 |
| T14 | 空闲检查、完整备份、受控停止与正式重启加载已执行 | 真实业务、WSL、桌面和 Agent 现场矩阵及恢复/回退演练仍待验收，AC24 未整体标为通过 |

详细证据见[M1 实现与验证记录](M1实现与验证记录_2026-10-13.md)、[M2 实现与验证记录](M2实现与验证记录_2026-10-13.md)和[M3 实现与验证记录](M3实现与验证记录_2026-10-14.md)。正式主控已空闲备份后重启加载 M1—M3，未新建正式测试任务或改变已有计划和 .env，未提交或推送；真实业务与现场剩余项不由本次隔离检查代替。

| M2 验收映射 | 主要代码与证据 | 状态范围 |
| --- | --- | --- |
| T05/T11 → AC09/AC20/AC21 | [workflows.py](../backend/app/services/workflows.py)、[API](../backend/app/api/workflows.py)、[test_workflow_api.py](../backend/tests/test_workflow_api.py) | 手动定义、权限、请求身份、整轮重跑及查询隔离检查；页面结果见 M2 记录 |
| T06/T07 → AC14/AC15/AC18/AC19 | [environments.py](../backend/app/environments.py)、[maintenance.py](../backend/app/maintenance.py)、[task_versions.py](../backend/app/task_versions.py)、[test_workflows.py](../backend/tests/test_workflows.py)、[test_workflow_runtime.py](../backend/tests/test_workflow_runtime.py) | 整轮冻结、全部未结束引用保护、删除摘要与维护隔离子场景 |
| T08/T09/T10 → AC09—AC13/AC17/AC18 | [execution_queue.py](../backend/app/services/execution_queue.py)、[runner.py](../backend/app/runner.py)、[native_process.py](../backend/app/native_process.py)、[test_workflow_runtime.py](../backend/tests/test_workflow_runtime.py) | 真实合成脚本、业务回执、停止、损坏快照、恢复和可信收尾；正式现场待 T14 |
| T12 → AC04—AC08/AC16/AC17/AC22 的流程计划部分 | [workflow_schedules.py](../backend/app/services/workflow_schedules.py)、[PlanEditor.vue](../frontend/src/features/schedule/PlanEditor.vue)、[M3 记录](M3实现与验证记录_2026-10-14.md) | 源码、后端、Vue、实际 Codex 页面及到点整轮检查完成，正式加载完成；真实业务与现场矩阵另行验收 |
