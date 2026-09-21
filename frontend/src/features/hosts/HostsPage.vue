<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { formatFileSize, formatTime } from '../../lib/format'
import { useWorkspaceContext } from '../../workspace/context'
import { activeRunStates, hostStateLabels, runStatusLabels, useHosts } from './useHosts'

const { me, handleSessionExpired } = useWorkspaceContext()
const props = defineProps({ openRunId: { type: Number, default: null } })
const emit = defineEmits(['run-opened'])
const {
  hosts, tasks, runs, loading, refreshing, loadError, actionError, notice, busy, enrollment,
  selectedHostId, selectedTaskId, selectedHost, selectedRunId, runDetail, detailLoading, detailError,
  approvedHosts, retryPending, refresh, createEnrollment, hostAction, setMode, dispatch, stopRun,
  activateCandidate, dismissCandidate,
  openRun, closeRun, loadRunDetail,
} = useHosts({ userId: me.value.id, onSessionExpired: handleSessionExpired })

const confirmation = ref(null)
const codeInput = ref(null)
const detailHeading = ref(null)
let runTrigger = null
const hostFilter = ref('all')
const runFilter = ref('all')
const pendingCount = computed(() => hosts.value.filter((host) => host.approval_status === 'pending').length)
const readyCount = computed(() => hosts.value.filter((host) => host.state === 'idle').length)
const activeCount = computed(() => runs.value.filter((run) => activeRunStates.has(run.status)).length)
const visibleHosts = computed(() => hosts.value.filter((host) => hostFilter.value === 'all'
  || (hostFilter.value === 'pending' ? host.approval_status === 'pending' : host.approval_status === 'approved')))
const visibleRuns = computed(() => runs.value.filter((run) => runFilter.value === 'all'
  || (runFilter.value === 'active' ? activeRunStates.has(run.status) : ['failed', 'timed_out', 'uncertain'].includes(run.status))))
const unavailableHostSelected = computed(() => selectedHostId.value && !approvedHosts.value.some((host) => host.id === Number(selectedHostId.value)))

function tone(state) {
  if (['idle', 'succeeded'].includes(state)) return 'good'
  if (['failed', 'timed_out', 'revoked', 'rejected'].includes(state)) return 'danger'
  if (['pending', 'uncertain', 'stopping'].includes(state)) return 'warning'
  if (['running', 'preparing', 'queued'].includes(state)) return 'info'
  return 'neutral'
}

function hostHint(host) {
  if (host.approval_status === 'pending') return '核对这台电脑的名称和机器标识后批准接入。'
  if (host.approval_status === 'rejected') return '申请已拒绝，这台电脑不能接收任务。'
  if (host.approval_status === 'revoked') return '身份已撤销，这台电脑不能继续连接主控。'
  if (host.state === 'uncertain') return '尚未确认旧运行已结束，继续保留占用，不派发新任务。'
  if (!host.online) return '等待这台电脑重新连接，已排队任务保持原目标。'
  if (host.state === 'stopping') return '停止请求已发出，等待执行端确认结束。'
  if (host.state === 'running') return '正在执行任务，后续任务会留在这台电脑的队列中。'
  if (!host.dispatch_enabled) return '主控调度已关闭；开启后仍需这台电脑本地同意调度。'
  if (!host.agent_dispatch_enabled) return '请在这台电脑上运行 start.ps1 -Dispatch，开启本机调度。'
  if (!host.interactive_session) return '这台电脑没有可用的交互式会话，请先登录 Windows 桌面。'
  return '已连接，主控和本机均允许调度，可以领取任务。'
}

function artifactUrl(value) {
  try {
    const url = new URL(value, window.location.origin)
    return url.origin === window.location.origin && url.pathname.startsWith('/api/') ? url.href : null
  } catch { return null }
}

function eventLabel(event) {
  return { started: '开始', stdout: '输出', stderr: '错误输出', finished: '结束', uncertain: '状态待确认' }[event.kind] || event.kind
}

async function showRun(id, event) {
  runTrigger = event?.currentTarget || null
  await openRun(id)
  await nextTick()
  detailHeading.value?.focus()
}

watch([() => props.openRunId, loading], async ([id, isLoading]) => {
  if (!id || isLoading) return
  await showRun(id)
  emit('run-opened')
}, { immediate: true })

async function closeDetails() {
  closeRun()
  await nextTick()
  if (runTrigger?.isConnected) runTrigger.focus()
}

async function confirmAction() {
  if (!confirmation.value) return
  const { host, action } = confirmation.value
  const result = await hostAction(host, action)
  if (result) confirmation.value = null
}

async function copyCode() {
  if (!enrollment.value) return
  try {
    if (!navigator.clipboard) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(enrollment.value.code)
    notice.value = '接入码已复制。'
  } catch {
    codeInput.value?.focus()
    codeInput.value?.select()
    notice.value = '接入码已选中，请按 Ctrl+C 复制。'
  }
}
</script>

<template>
  <section class="hosts-page" aria-labelledby="hosts-heading">
    <header class="host-heading">
      <div>
        <h2 id="hosts-heading">宿主机</h2>
        <p>管理局域网 Windows 电脑，向指定机器分发任务。</p>
      </div>
      <button class="host-button" type="button" :disabled="refreshing" @click="refresh()">
        {{ refreshing ? '正在刷新…' : '刷新状态' }}
      </button>
    </header>

    <div class="host-scope-note">
      <strong>当前支持指定机器手动运行普通 Python 任务。</strong>
      <span>带输入模板、受限环境和原生 DP 专用任务暂不支持。远程定时计划、托盘与桌面状态条尚未接入；远程失败会生成站内提醒，并按任务开关发送飞书摘要，AI 候选需管理员确认后才会重跑。</span>
    </div>
    <p v-if="notice" class="host-feedback success" role="status">{{ notice }}</p>
    <p v-if="actionError" class="host-feedback error" role="alert">{{ actionError }}</p>
    <div v-if="loadError" class="host-feedback error" role="alert">
      <span>状态刷新失败：{{ loadError }}。已有内容可能不是最新状态。</span>
      <button class="host-button" type="button" :disabled="refreshing" @click="refresh()">重新载入</button>
    </div>
    <div v-if="loading" class="host-empty" role="status">正在载入宿主机和远程运行…</div>

    <template v-else>
      <div class="host-metrics" aria-label="宿主机概况">
        <div><span>已登记电脑</span><strong>{{ hosts.length }}</strong></div>
        <div><span>待批准</span><strong>{{ pendingCount }}</strong></div>
        <div><span>调度空闲</span><strong>{{ readyCount }}</strong></div>
        <div><span>近期未结束</span><strong>{{ activeCount }}</strong></div>
      </div>

      <div class="host-setup-grid">
        <section class="host-panel" aria-labelledby="enrollment-heading">
          <header class="host-section-heading"><h3 id="enrollment-heading">接入新电脑</h3></header>
          <ol class="host-steps">
            <li>下载 Agent 并解压到另一台 Windows 电脑，按包内说明启动。</li>
            <li>生成接入码并下载 Agent，在新电脑解压后双击“安装并接入Agent”。</li>
            <li>新电脑只需填写主控地址、接入码和电脑名称，不需要打开 PowerShell。</li>
            <li>电脑出现在下方待批准列表，管理员核对后批准。</li>
            <li>开启主控调度，并在电脑上运行 <code>start.ps1 -Dispatch</code>。</li>
          </ol>
          <p class="host-help">Agent 需要在已登录的 Windows 桌面中运行。新电脑不需要部署数据库、模型或知识库。当前主控和 Agent 不能在同一台电脑同时运行，请先使用另一台电脑接入。</p>
          <div class="host-card-actions">
            <button class="host-button primary" type="button" :disabled="Boolean(busy)" @click="createEnrollment">
              {{ busy === 'enrollment' ? '正在生成…' : '生成一次性接入码' }}
            </button>
            <a class="host-button" href="/api/hosts/agent-download" download>下载 Agent 安装包</a>
          </div>
          <div v-if="enrollment" class="host-enrollment" aria-live="polite">
            <label for="host-enrollment-code">接入码</label>
            <div class="host-copy-row">
              <input id="host-enrollment-code" ref="codeInput" :value="enrollment.code" readonly autocomplete="off" spellcheck="false" @focus="$event.target.select()" />
              <button class="host-button" type="button" @click="copyCode">复制</button>
            </div>
            <p>有效期至 {{ formatTime(enrollment.expires_at) }}，仅供一台电脑申请一次。接入码只在生成时返回，离开本页后不再显示。</p>
            <label for="host-server-url">主控地址</label>
            <input id="host-server-url" :value="enrollment.server_url" readonly @focus="$event.target.select()" />
            <p>请使用新电脑能访问的局域网地址；如果这里显示 127.0.0.1 或 localhost，请换成主控电脑的局域网 IP。</p>
            <button class="host-button quiet" type="button" @click="enrollment = null">隐藏接入码</button>
          </div>
        </section>

        <section class="host-panel" aria-labelledby="dispatch-heading">
          <header class="host-section-heading"><h3 id="dispatch-heading">分发任务</h3></header>
          <form class="host-dispatch-form" @submit.prevent="dispatch">
            <label for="dispatch-host">目标宿主机</label>
            <select id="dispatch-host" v-model="selectedHostId" :disabled="busy === 'dispatch' || !approvedHosts.length" required>
              <option value="" disabled>{{ approvedHosts.length ? '选择已批准的电脑' : '请先批准一台宿主机' }}</option>
              <option v-if="unavailableHostSelected" :value="selectedHostId" disabled>{{ selectedHost ? `${selectedHost.name} · ${hostStateLabels[selectedHost.state] || '不可用'}` : '原宿主机不可用，请重新选择' }}</option>
              <option v-for="host in approvedHosts" :key="host.id" :value="String(host.id)">{{ host.name }} · {{ hostStateLabels[host.state] || host.state }}</option>
            </select>
            <p v-if="selectedHost" class="host-help">{{ hostHint(selectedHost) }}</p>
            <label for="dispatch-task">任务</label>
            <select id="dispatch-task" v-model="selectedTaskId" :disabled="busy === 'dispatch' || !tasks.length" required>
              <option value="" disabled>{{ tasks.length ? '选择现有普通 Python 任务' : '请先在任务中心创建任务' }}</option>
              <option v-for="task in tasks" :key="task.id" :value="String(task.id)">{{ task.name }}</option>
            </select>
            <p class="host-help">提交时保存本次源码与依赖快照。目标机器离线、忙碌或非调度时保持排队，不会自动换电脑。</p>
            <p v-if="retryPending" class="host-inline-warning">上次分发未完成。重试将沿用同一请求编号，避免重复创建运行。</p>
            <button class="host-button primary" type="submit" :disabled="Boolean(busy) || !selectedHostId || !selectedTaskId || selectedHost?.approval_status !== 'approved'">
              {{ busy === 'dispatch' ? '正在分发…' : retryPending ? '重试分发' : '分发到这台电脑' }}
            </button>
          </form>
        </section>
      </div>

      <section class="host-panel" aria-labelledby="host-list-heading">
        <header class="host-section-heading">
          <div><h3 id="host-list-heading">已登记的宿主机</h3><p>心跳自动刷新。主控和本机均允许调度，且有交互式会话时才会领取任务。</p></div>
          <div class="host-filter"><label for="host-filter">显示</label><select id="host-filter" v-model="hostFilter"><option value="all">全部电脑</option><option value="pending">待批准</option><option value="approved">已批准</option></select></div>
        </header>
        <div v-if="!visibleHosts.length" class="host-empty"><strong>{{ hosts.length ? '没有符合条件的电脑' : '还没有电脑申请接入' }}</strong><p>{{ hosts.length ? '切换筛选查看其他电脑。' : '生成接入码，在另一台 Windows 电脑启动 Agent 后，申请会显示在这里。' }}</p></div>
        <div v-else class="host-list">
          <article v-for="host in visibleHosts" :key="host.id" class="host-card">
            <div class="host-card-header"><div class="host-name"><h4>{{ host.name }}</h4><span class="host-subtle">#{{ host.id }} · Agent {{ host.agent_version || '版本未知' }}</span></div><span class="host-badge" :class="tone(host.state)"><i aria-hidden="true"></i>{{ hostStateLabels[host.state] || host.state }}</span></div>
            <p class="host-machine-id">机器标识：<code>{{ host.machine_id }}</code></p>
            <p class="host-card-hint">{{ hostHint(host) }}</p>
            <dl class="host-facts">
              <div><dt>连接</dt><dd>{{ host.online ? '在线' : '离线' }}</dd></div>
              <div><dt>主控调度</dt><dd>{{ host.dispatch_enabled ? '已开启' : '已关闭' }}</dd></div>
              <div><dt>本机调度</dt><dd>{{ host.agent_dispatch_enabled ? '已开启' : '已关闭' }}</dd></div>
              <div><dt>桌面会话</dt><dd>{{ host.interactive_session ? '可用' : '不可用' }}</dd></div>
            </dl>
            <div class="host-card-footer"><span class="host-subtle">最近心跳 {{ formatTime(host.last_seen_at) }}</span>
              <div class="host-card-actions">
                <template v-if="host.approval_status === 'pending'">
                  <button class="host-button primary" type="button" :disabled="Boolean(busy)" @click="hostAction(host, 'approve')">{{ busy === `host:${host.id}` ? '处理中…' : '批准接入' }}</button>
                  <button class="host-button" type="button" :disabled="Boolean(busy)" @click="confirmation = { host, action: 'reject' }">拒绝</button>
                </template>
                <template v-else-if="host.approval_status === 'approved'">
                  <button v-if="host.active_run_id" class="host-button" type="button" @click="showRun(host.active_run_id, $event)">查看运行 #{{ host.active_run_id }}</button>
                  <button class="host-button" type="button" :disabled="Boolean(busy) || Boolean(host.active_run_id)" @click="setMode(host)">{{ busy === `host:${host.id}` ? '处理中…' : host.dispatch_enabled ? '关闭主控调度' : '开启主控调度' }}</button>
                  <button class="host-button danger" type="button" :disabled="Boolean(busy)" @click="confirmation = { host, action: 'revoke' }">撤销接入</button>
                </template>
              </div>
            </div>
            <p v-if="host.active_run_id && host.approval_status === 'approved'" class="host-help">更改调度模式前，请先停止当前运行并等待电脑确认。</p>
            <div v-if="confirmation?.host.id === host.id" class="host-confirm" @keydown.esc="confirmation = null">
              <p>{{ confirmation.action === 'revoke' ? `撤销「${host.name}」后，它将无法继续连接主控。` : `拒绝「${host.name}」的接入申请？` }}</p>
              <div class="host-card-actions"><button class="host-button" type="button" :disabled="Boolean(busy)" @click="confirmation = null">取消</button><button class="host-button danger" type="button" :disabled="Boolean(busy)" @click="confirmAction">{{ busy ? '处理中…' : confirmation.action === 'revoke' ? '确认撤销' : '确认拒绝' }}</button></div>
            </div>
          </article>
        </div>
      </section>

      <section class="host-panel" aria-labelledby="remote-runs-heading">
        <header class="host-section-heading"><div><h3 id="remote-runs-heading">远程运行记录</h3><p>最近 {{ runs.length }} 条，最多显示 100 条。本机旧运行仍在运行中心查看。</p></div><div class="host-filter"><label for="run-filter">显示</label><select id="run-filter" v-model="runFilter"><option value="all">全部状态</option><option value="active">尚未结束</option><option value="attention">失败或待确认</option></select></div></header>
        <div v-if="!visibleRuns.length" class="host-empty"><strong>{{ runs.length ? '没有符合条件的运行' : '还没有远程运行' }}</strong><p>{{ runs.length ? '切换筛选查看其他状态。' : '选择任务和目标宿主机后，运行记录会显示在这里。' }}</p></div>
        <ul v-else class="host-runs">
          <li v-for="run in visibleRuns" :key="run.id" :class="{ selected: selectedRunId === run.id }">
            <div class="host-run-summary"><strong>{{ run.task_name }}</strong><span>#{{ run.id }} · v{{ run.version_sequence ?? '?' }} · {{ run.host_name }} · {{ formatTime(run.created_at) }}</span><p v-if="run.waiting_reason">{{ run.waiting_reason }}</p></div>
            <div class="host-card-actions"><span class="host-badge" :class="tone(run.status)"><i aria-hidden="true"></i>{{ runStatusLabels[run.status] || run.status }}</span><button class="host-button" type="button" :aria-label="`查看运行 #${run.id} ${run.task_name} 的日志与结果`" :aria-expanded="selectedRunId === run.id" aria-controls="remote-run-detail" @click="showRun(run.id, $event)">日志与结果</button></div>
          </li>
        </ul>
      </section>

      <section v-if="selectedRunId != null" id="remote-run-detail" class="host-panel host-detail" aria-labelledby="run-detail-heading">
        <header class="host-section-heading"><h3 id="run-detail-heading" ref="detailHeading" tabindex="-1">运行 #{{ selectedRunId }} · 日志与结果</h3><button class="host-button" type="button" @click="closeDetails">收起详情</button></header>
        <p v-if="detailLoading" class="host-help" role="status">正在载入运行详情…</p>
        <div v-if="detailError" class="host-feedback error" role="alert"><span>运行详情刷新失败：{{ detailError }}</span><button class="host-button" type="button" @click="loadRunDetail(selectedRunId)">重试</button></div>
        <template v-if="runDetail">
          <div class="host-detail-summary"><div><strong>{{ runDetail.task_name }} · v{{ runDetail.version_sequence ?? '?' }}</strong><p>{{ runDetail.host_name }} · <span class="host-badge" :class="tone(runDetail.status)"><i aria-hidden="true"></i>{{ runStatusLabels[runDetail.status] || runDetail.status }}</span></p></div><button v-if="activeRunStates.has(runDetail.status) && !runDetail.stop_requested" class="host-button danger" type="button" :disabled="Boolean(busy)" @click="stopRun(runDetail)">{{ busy === `stop:${runDetail.id}` ? '正在请求…' : runDetail.status === 'queued' ? '取消排队' : '请求停止' }}</button></div>
          <p v-if="runDetail.stop_requested && activeRunStates.has(runDetail.status)" class="host-inline-warning">已请求停止，正在等待宿主机确认。请求发出不代表进程已经结束。</p>
          <p v-if="runDetail.status === 'uncertain'" class="host-inline-warning">连接中断或重启后，旧运行尚未确认结束。宿主机继续保持占用，不会接收新任务。</p>
          <p v-if="runDetail.waiting_reason" class="host-help">{{ runDetail.waiting_reason }}</p>
          <p v-if="runDetail.error" class="host-feedback" :class="runDetail.status === 'succeeded' ? 'success' : ['failed', 'timed_out'].includes(runDetail.status) ? 'error' : ''">{{ runDetail.error }}</p>
          <div v-if="runDetail.collection_progress" class="host-progress">
            <strong>采集进度</strong>
            <p>{{ runDetail.collection_progress.latest.stage || '任务上报' }}<template v-if="runDetail.collection_progress.latest.page != null"> · 第 {{ runDetail.collection_progress.latest.page }} 页</template><template v-if="runDetail.collection_progress.latest.collected != null"> · 已采集 {{ runDetail.collection_progress.latest.collected }} 条</template><template v-if="runDetail.collection_progress.latest.total != null"> / {{ runDetail.collection_progress.latest.total }} 条</template></p>
            <small>{{ runDetail.collection_progress.latest.message || `已收到 ${runDetail.collection_progress.event_count} 条结构化进度` }}</small>
          </div>
          <div v-if="runDetail.maintenance" class="host-maintenance" :class="{ ready: runDetail.maintenance.status === 'candidate' }">
            <div><strong>AI 维护建议</strong><p v-if="['pending','generating'].includes(runDetail.maintenance.status)">正在根据冻结版本和远程失败日志生成参考建议。不会自动启用或运行。</p><p v-else-if="runDetail.maintenance.status === 'candidate'">已生成候选 v{{ runDetail.maintenance.sequence }}。该版本尚未试跑，确认后才会启用并在原宿主机创建一条新运行。</p><p v-else-if="runDetail.maintenance.status === 'activated'">候选已由管理员确认；远程重跑 #{{ runDetail.maintenance.rerun_remote_run_id }} 已单独记录。</p><p v-else>{{ runDetail.maintenance.note || '本次未生成代码候选。' }}</p></div>
            <div v-if="runDetail.maintenance.status === 'candidate'" class="host-maintenance-actions"><a class="host-button" :href="`/api/task-versions/versions/${runDetail.maintenance.candidate_version_id}/download`" download>下载候选源码</a><button class="host-button" type="button" :disabled="Boolean(busy)" @click="dismissCandidate(runDetail.maintenance)">暂不使用</button><button class="host-button primary" type="button" :disabled="Boolean(busy)" @click="activateCandidate(runDetail.maintenance)">{{ busy === `candidate:${runDetail.maintenance.update_id}` ? '正在确认…' : '确认并重新运行' }}</button></div>
          </div>
          <dl class="host-run-facts"><div><dt>提交时间</dt><dd>{{ formatTime(runDetail.created_at) }}</dd></div><div><dt>开始时间</dt><dd>{{ formatTime(runDetail.started_at) }}</dd></div><div><dt>结束时间</dt><dd>{{ formatTime(runDetail.finished_at) }}</dd></div><div><dt>退出码</dt><dd>{{ runDetail.exit_code ?? '—' }}</dd></div></dl>
          <details class="host-technical"><summary>执行快照</summary><p>本次运行固定使用 v{{ runDetail.version_sequence ?? '?' }}（内部版本 #{{ runDetail.code_version_id ?? '—' }}），代码与依赖内容不可变。</p><code>{{ runDetail.package_hash }}</code></details>
          <h4 class="host-subheading">运行日志</h4>
          <p v-if="runDetail.events_truncated" class="host-help">仅显示最近 500 条日志，完整事件仍保存在主控。</p>
          <div v-if="runDetail.events?.length" class="host-log" tabindex="0" role="region" aria-label="运行原始日志，可使用键盘滚动">
            <div v-for="event in runDetail.events" :key="event.seq" class="host-log-entry" :class="{ 'log-error': event.kind === 'stderr' }"><span class="host-log-label">#{{ event.seq }} {{ eventLabel(event) }}{{ event.status ? ' · ' + (runStatusLabels[event.status] || event.status) : '' }}</span><pre v-if="event.text">{{ event.text }}</pre></div>
          </div>
          <p v-else class="host-help">{{ runDetail.status === 'queued' ? '任务尚未开始，等待目标电脑领取。' : '执行端尚未回传日志。' }}</p>
          <h4 class="host-subheading">结果文件</h4>
          <ul v-if="runDetail.artifacts?.length" class="host-artifacts"><li v-for="artifact in runDetail.artifacts" :key="artifact.name"><div><strong>{{ artifact.name }}</strong><span>{{ formatFileSize(artifact.size) }}</span></div><a v-if="artifactUrl(artifact.download_url)" class="host-button" :href="artifactUrl(artifact.download_url)" :aria-label="`下载 ${artifact.name}`" download>下载</a><span v-else class="host-help">下载地址不可用</span></li></ul>
          <p v-else class="host-help">暂未收到结果文件。脚本需要把结果写入本次产物目录。</p>
        </template>
      </section>
    </template>
  </section>
</template>

<style scoped>
.hosts-page { --host-green: #009139; --host-ink: #221814; --host-text: #292b28; --host-muted: #656d65; --host-border: #dfe3df; --host-soft: #f7f8f6; display: grid; width: min(1180px, 100%); min-width: 0; margin: 0 auto; gap: 16px; color: var(--host-text); font: 13px/1.55 Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; }
.hosts-page *, .hosts-page *::before, .hosts-page *::after { box-sizing: border-box; }
.hosts-page h2, .hosts-page h3, .hosts-page h4, .hosts-page p, .hosts-page dl { margin: 0; }
.hosts-page h2 { color: var(--host-ink); font-size: 20px; line-height: 28px; }
.hosts-page h3 { color: var(--host-ink); font-size: 15px; line-height: 22px; }
.hosts-page h4 { color: var(--host-ink); font-size: 14px; line-height: 20px; }
.hosts-page code { font-family: 'SFMono-Regular', Consolas, monospace; overflow-wrap: anywhere; }
.host-heading, .host-section-heading, .host-card-header, .host-card-footer, .host-detail-summary { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-width: 0; }
.host-heading p, .host-section-heading p { margin-top: 4px; color: var(--host-muted); font-size: 12px; }
.host-heading { align-items: flex-start; }
.host-button { display: inline-flex; flex: 0 0 auto; align-items: center; justify-content: center; min-height: 36px; padding: 7px 12px; border: 1px solid var(--host-border); border-radius: 8px; background: #fff; color: var(--host-text); font-family: inherit; font-size: 13px; font-weight: 500; line-height: 20px; text-decoration: none; white-space: nowrap; cursor: pointer; transition: background-color 140ms, border-color 140ms, transform 140ms; }
.host-button:hover:not(:disabled) { border-color: #aeb8ae; background: var(--host-soft); }
.host-button:active:not(:disabled) { transform: translateY(1px); }
.host-button.primary { border-color: var(--host-green); background: var(--host-green); color: #fff; }
.host-button.primary:hover:not(:disabled) { border-color: #007e31; background: #007e31; }
.host-button.danger { border-color: #f0d3ca; background: #fff4f0; color: #a53721; }
.host-button.danger:hover:not(:disabled) { border-color: #df9e8d; background: #ffe9e1; }
.host-button.quiet { border-color: transparent; background: transparent; }
.host-button:disabled, .hosts-page input:disabled, .hosts-page select:disabled { opacity: .55; cursor: not-allowed; }
.hosts-page :is(button, a, input, select, summary, [tabindex]):focus-visible { outline: 3px solid #00913945; outline-offset: 3px; }
.host-scope-note { display: grid; gap: 4px; padding: 12px 16px; border: 1px solid var(--host-border); border-radius: 8px; background: var(--host-soft); }
.host-scope-note strong { font-size: 13px; font-weight: 600; }
.host-scope-note span { font-size: 12px; color: var(--host-muted); }
.host-feedback { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; padding: 12px 16px; border: 1px solid var(--host-border); border-radius: 8px; overflow-wrap: anywhere; }
.host-feedback.success { background: #edf8f0; border-color: #bddfc9; color: #176b35; }
.host-feedback.error { background: #fff4f0; border-color: #efd0c6; color: #a53721; }
.host-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.host-metrics > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 12px 16px; border: 1px solid var(--host-border); border-radius: 12px; background: #fff; }
.host-metrics span { color: var(--host-muted); font-size: 12px; }
.host-metrics strong { color: var(--host-ink); font-size: 24px; line-height: 32px; font-weight: 600; }
.host-setup-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; align-items: start; }
.host-panel { min-width: 0; padding: 20px; border: 1px solid var(--host-border); border-radius: 12px; background: #fff; }
.host-section-heading { margin-bottom: 16px; align-items: flex-start; }
.host-steps { display: grid; gap: 8px; margin: 0 0 12px; padding-left: 20px; }
.host-help { color: var(--host-muted); font-size: 12px; line-height: 20px; overflow-wrap: anywhere; }
.host-panel > .host-help { margin-bottom: 12px; }
.host-maintenance { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 12px 0; padding: 14px 16px; border: 1px solid #ead8b5; border-radius: 10px; background: #fffaf0; }
.host-maintenance.ready { border-color: #b9ddc6; background: #f5fbf6; }
.host-maintenance p { margin-top: 3px; color: var(--host-muted); font-size: 12px; }
.host-progress { display: grid; gap: 3px; margin: 12px 0; padding: 14px 16px; border: 1px solid #cfe1d5; border-radius: 10px; background: #f6faf7; }
.host-progress p { color: var(--host-text); }
.host-progress small { color: var(--host-muted); }
.host-maintenance-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.host-enrollment { display: grid; gap: 8px; margin-top: 16px; padding: 16px; border: 1px solid #bddfc9; border-radius: 8px; background: #f5fbf6; }
.host-enrollment p { color: var(--host-muted); font-size: 12px; }
.host-enrollment > .host-button { justify-self: start; }
.host-copy-row { display: flex; gap: 8px; }
.host-copy-row input { font-family: Consolas, monospace; }
.hosts-page label { font-size: 12px; font-weight: 600; }
.hosts-page input, .hosts-page select { width: 100%; min-width: 0; height: 38px; padding: 0 11px; border: 1px solid var(--host-border); border-radius: 8px; background: #fff; color: var(--host-text); font-family: inherit; font-size: 13px; line-height: 20px; }
.hosts-page input:hover, .hosts-page select:hover { border-color: #b8c2b8; }
.hosts-page input:focus, .hosts-page select:focus { border-color: var(--host-green); }
.host-dispatch-form { display: grid; gap: 8px; }
.host-dispatch-form > label:not(:first-child) { margin-top: 4px; }
.host-dispatch-form > .host-button { margin-top: 8px; justify-self: start; }
.host-inline-warning { padding: 10px 12px; border: 1px solid #e7dca8; border-radius: 8px; background: #fffbeb; color: #715912; font-size: 12px; overflow-wrap: anywhere; }
.host-filter { display: flex; flex: 0 0 auto; align-items: center; gap: 8px; }
.host-filter select { width: auto; min-width: 110px; }
.host-list { display: grid; gap: 12px; }
.host-card { min-width: 0; padding: 16px; border: 1px solid var(--host-border); border-radius: 8px; }
.host-name { min-width: 0; }
.host-name h4 { overflow-wrap: anywhere; }
.host-subtle { color: var(--host-muted); font-size: 11px; }
.host-badge { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 5px; padding: 3px 7px; border-radius: 6px; background: #f0f2ef; color: #5d655d; font-size: 11px; line-height: 18px; white-space: nowrap; }
.host-badge i { width: 6px; height: 6px; flex: 0 0 auto; border-radius: 50%; background: currentColor; }
.host-badge.good { background: #eaf6ed; color: #167536; }
.host-badge.danger { background: #fff0ea; color: #a53721; }
.host-badge.warning { background: #fcf6db; color: #775e16; }
.host-badge.info { background: #eaf2f4; color: #365f6b; }
.host-machine-id { margin-top: 8px !important; color: var(--host-muted); font-size: 11px; }
.host-card-hint { margin: 8px 0 12px !important; font-size: 12px; }
.host-facts, .host-run-facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.host-facts div, .host-run-facts div { min-width: 0; }
.host-facts dt, .host-run-facts dt { color: var(--host-muted); font-size: 11px; }
.host-facts dd, .host-run-facts dd { margin: 4px 0 0; font-size: 12px; overflow-wrap: anywhere; }
.host-card-footer { margin-top: 12px; padding-top: 12px; border-top: 1px solid #ecefec; flex-wrap: wrap; }
.host-card-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.host-card > .host-help { margin-top: 8px; }
.host-confirm { display: grid; gap: 12px; margin-top: 12px; padding: 12px; border: 1px solid #efd0c6; border-radius: 8px; background: #fff8f5; }
.host-confirm .host-card-actions { justify-content: flex-end; }
.host-empty { display: grid; gap: 4px; padding: 28px 16px; border-radius: 8px; background: var(--host-soft); text-align: center; }
.host-empty strong { font-size: 14px; font-weight: 600; }
.host-empty p { color: var(--host-muted); font-size: 12px; }
.host-runs, .host-artifacts { list-style: none; margin: 0; padding: 0; }
.host-runs > li { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 12px; border: 1px solid transparent; border-bottom-color: #ecefec; border-radius: 8px; }
.host-runs > li.selected { border-color: #bddfc9; background: #f5fbf6; }
.host-run-summary { display: grid; min-width: 0; gap: 3px; }
.host-run-summary strong { font-size: 13px; overflow-wrap: anywhere; }
.host-run-summary span, .host-run-summary p { color: var(--host-muted); font-size: 12px; overflow-wrap: anywhere; }
.host-run-summary + .host-card-actions { flex-shrink: 0; }
.host-detail-summary { margin-bottom: 16px; align-items: flex-start; }
.host-detail-summary strong { font-size: 14px; overflow-wrap: anywhere; }
.host-detail-summary p { margin-top: 4px; color: var(--host-muted); }
.host-detail > .host-inline-warning, .host-detail > .host-feedback { margin-bottom: 12px; }
.host-technical { margin-top: 16px; border: 1px solid var(--host-border); border-radius: 8px; padding: 10px 12px; }
.host-technical summary { cursor: pointer; font-size: 12px; }
.host-technical p { margin: 8px 0; color: var(--host-muted); font-size: 12px; }
.host-technical code { font-size: 11px; }
.host-subheading { margin: 20px 0 8px !important; }
.host-log { max-height: 440px; overflow: auto; padding: 12px; border: 1px solid var(--host-border); border-radius: 8px; background: #f8f9f7; overscroll-behavior: contain; }
.host-log-entry + .host-log-entry { padding-top: 10px; margin-top: 10px; border-top: 1px solid #e5e9e3; }
.host-log-label { color: var(--host-muted); font-size: 11px; }
.host-log-entry pre { margin: 4px 0 0; padding: 0; max-height: none; border: 0; border-radius: 0; background: transparent; color: var(--host-text); font: 12px/20px 'SFMono-Regular', Consolas, monospace; white-space: pre-wrap; overflow: visible; overflow-wrap: anywhere; word-break: break-word; }
.host-log-entry.log-error pre { color: #a53721; }
.host-artifacts li { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 0; border-bottom: 1px solid #ecefec; }
.host-artifacts li > div { display: grid; min-width: 0; gap: 4px; }
.host-artifacts strong { font-size: 12px; overflow-wrap: anywhere; }
.host-artifacts span { color: var(--host-muted); font-size: 11px; }
@media (max-width: 1000px) { .host-setup-grid { grid-template-columns: minmax(0, 1fr); } .host-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 720px) { .host-panel { padding: 16px; } .host-section-heading { flex-wrap: wrap; } .host-facts, .host-run-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); } .host-runs > li { align-items: flex-start; flex-direction: column; padding: 12px 8px; } .host-card-footer, .host-maintenance { align-items: flex-start; flex-direction: column; } .host-maintenance-actions { width: 100%; justify-content: flex-start; } .host-run-summary + .host-card-actions { align-self: stretch; justify-content: space-between; } }
@media (max-width: 400px) { .host-heading { flex-wrap: wrap; } .host-panel { padding: 12px; } .host-card { padding: 12px; } .host-metrics { gap: 8px; } .host-metrics > div { padding: 8px 12px; } .host-metrics strong { font-size: 20px; } .host-card-header { align-items: flex-start; } .host-badge { padding: 3px 5px; } .host-card-actions { gap: 6px; } .host-button { padding: 7px 10px; } .host-enrollment { padding: 12px; } .host-detail-summary { flex-wrap: wrap; } }
@media (prefers-reduced-motion: reduce) { .host-button { transition: none; } .host-button:active:not(:disabled) { transform: none; } }
</style>
