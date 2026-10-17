<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { formatFileSize, formatTime } from '../../lib/format'
import { useWorkspaceContext } from '../../workspace/context'
import { activeRunStates, hostStateLabels, runStatusLabels, useHosts } from './useHosts'
import EnvironmentDetails from './EnvironmentDetails.vue'

const { handleSessionExpired, isSuperAdmin } = useWorkspaceContext()
const props = defineProps({ openRunId: { type: Number, default: null } })
const emit = defineEmits(['run-opened'])
const {
  hosts, runs, loading, refreshing, loadError, actionError, notice, busy, enrollment,
  selectedRunId, runDetail, detailLoading, detailError,
  refresh, createEnrollment, hostAction, upgradeHost, setMode, stopRun,
  activateCandidate, dismissCandidate,
  openRun, closeRun, loadRunDetail,
} = useHosts({ onSessionExpired: handleSessionExpired })

const confirmation = ref(null)
const codeInput = ref(null)
const detailHeading = ref(null)
const connectOpen = ref(false)
const expandedHosts = ref(new Set())
let runTrigger = null
const hostFilter = ref('current')
const runFilter = ref('all')
const pendingCount = computed(() => hosts.value.filter((host) => host.approval_status === 'pending').length)
const readyCount = computed(() => hosts.value.filter((host) => host.state === 'idle').length)
const activeCount = computed(() => runs.value.filter((run) => activeRunStates.has(run.status)).length)
const visibleHosts = computed(() => hosts.value.filter((host) => hostFilter.value === 'all'
  || (hostFilter.value === 'current' ? ['pending', 'approved'].includes(host.approval_status) : host.approval_status === hostFilter.value)))
const visibleRuns = computed(() => runs.value.filter((run) => runFilter.value === 'all'
  || (runFilter.value === 'active' ? activeRunStates.has(run.status) : ['failed', 'timed_out', 'uncertain'].includes(run.status))))

function tone(state) {
  if (['idle', 'succeeded'].includes(state)) return 'good'
  if (['failed', 'timed_out', 'revoked', 'rejected'].includes(state)) return 'danger'
  if (['pending', 'uncertain', 'stopping'].includes(state)) return 'warning'
  if (['running', 'preparing', 'queued'].includes(state)) return 'info'
  return 'neutral'
}

function hostHint(host) {
  if (host.approval_status === 'pending') return '核对机器标识后批准'
  if (['rejected', 'revoked'].includes(host.approval_status)) return ''
  if (host.upgrade_pending) return '暂缓新派发'
  if (host.state === 'uncertain') return '旧运行尚未确认结束，暂停派发新任务。'
  if (!host.online) return '等待重新连接，排队任务保留原目标。'
  if (host.state === 'stopping') return '已请求停止，等待执行端确认。'
  if (host.state === 'running') return ''
  if (host.environment_reporting_supported === false) return '升级 Agent 至 0.4.0 后可校验业务环境并回传依赖版本'
  if (!host.dispatch_enabled) return '主控调度已关闭'
  if (!host.agent_dispatch_enabled) return '本机调度已关闭，请在 Agent 托盘开启'
  if (!host.interactive_session) return '请先登录此电脑的 Windows 桌面。'
  return ''
}

function upgradeReason(host) {
  if (!host.remote_upgrade_supported) return '首次需在目标电脑更新到 0.5.0'
  if (!host.online) return '目标电脑离线'
  if (host.upgrade_pending) return '正在升级或等待结果确认'
  if (host.active_run_id) return '任务结束后可升级'
  return `升级至 ${host.current_agent_version}`
}

function upgradeLabel(job) {
  return { queued: '等待升级', downloading: '正在下载', installing: '正在升级',
    awaiting_reconnect: '等待重连', succeeded: `已升级至 ${job.target_version}`,
    failed: '升级失败', uncertain: '升级结果待确认', expired: '升级请求已过期', cancelled: '升级已取消' }[job.status] || job.status
}

function toggleHostDetails(id) {
  if (expandedHosts.value.has(id)) expandedHosts.value.delete(id)
  else expandedHosts.value.add(id)
}

function artifactUrl(value) {
  try {
    const url = new URL(value, window.location.origin)
    return url.origin === window.location.origin && url.pathname.startsWith('/api/') ? url.href : null
  } catch { return null }
}

function eventLabel(event) {
  return { started: '开始', stdout: '输出', stderr: '错误输出', finished: '结束', uncertain: '状态待确认', environment: '环境校验' }[event.kind] || event.kind
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
  <section class="hosts-page" aria-label="宿主机管理">
    <p v-if="notice" class="host-feedback success" role="status">{{ notice }}</p>
    <p v-if="actionError" class="host-feedback error" role="alert">{{ actionError }}</p>
    <div v-if="loadError" class="host-feedback error" role="alert">
      <span>状态刷新失败：{{ loadError }}。已有内容可能不是最新状态。</span>
      <button class="host-button" type="button" :disabled="refreshing" @click="refresh()">重新载入</button>
    </div>
    <div v-if="loading" class="host-empty" role="status">正在载入宿主机和远程运行…</div>

    <template v-else>
      <section class="host-panel host-computers" aria-labelledby="host-list-heading">
        <header class="host-section-heading host-list-heading">
          <div class="host-list-title"><h2 id="host-list-heading">电脑</h2><span>{{ visibleHosts.length }} 台</span></div>
          <div class="host-toolbar">
            <select id="host-filter" v-model="hostFilter" aria-label="电脑范围"><option value="current">当前接入</option><option value="pending">待批准</option><option value="approved">已批准</option><option value="all">全部电脑</option></select>
            <button class="host-button" type="button" :disabled="refreshing" @click="refresh()">{{ refreshing ? '正在刷新…' : '刷新' }}</button>
            <button class="host-button" :class="{ primary: !connectOpen }" type="button" :aria-expanded="connectOpen" aria-controls="host-enrollment-panel" @click="connectOpen = !connectOpen">{{ connectOpen ? '收起接入' : '接入新电脑' }}</button>
          </div>
        </header>
        <div class="host-summary" aria-label="宿主机概况"><span>已登记 <strong>{{ hosts.length }}</strong></span><span>待批准 <strong>{{ pendingCount }}</strong></span><span>可调度 <strong>{{ readyCount }}</strong></span><span>未结束运行 <strong>{{ activeCount }}</strong></span></div>

        <section v-show="connectOpen" id="host-enrollment-panel" class="host-connect" aria-label="接入新电脑">
          <div class="host-card-actions">
            <button class="host-button primary" type="button" :disabled="Boolean(busy)" @click="createEnrollment">{{ busy === 'enrollment' ? '正在生成…' : '生成接入码' }}</button>
            <a class="host-button" href="/api/hosts/agent-download" download>下载 Agent 安装包</a>
          </div>
          <details class="host-guide">
            <summary>接入步骤</summary>
            <ol class="host-steps">
              <li>在另一台已登录 Windows 桌面的电脑上，解压 Agent，双击“安装并接入Agent”，填写主控地址、接入码和电脑名称。</li>
              <li>在下方核对名称与机器标识，批准接入。</li>
              <li>主控和 Agent 都开启调度后，在任务中心选择运行电脑。</li>
            </ol>
          </details>
          <div v-if="enrollment" class="host-enrollment" aria-live="polite">
            <label for="host-enrollment-code">接入码</label>
            <div class="host-copy-row"><input id="host-enrollment-code" ref="codeInput" :value="enrollment.code" readonly autocomplete="off" spellcheck="false" @focus="$event.target.select()" /><button class="host-button" type="button" @click="copyCode">复制</button></div>
            <p>{{ formatTime(enrollment.expires_at) }} 到期 · 仅限一台电脑 · 离开本页后无法再次查看</p>
            <label for="host-server-url">主控地址</label>
            <input id="host-server-url" :value="enrollment.server_url" readonly @focus="$event.target.select()" />
            <p>若为 127.0.0.1 或 localhost，请改用主控的局域网 IP。</p>
            <button class="host-button quiet" type="button" @click="enrollment = null">隐藏接入码</button>
          </div>
        </section>

        <div v-if="!visibleHosts.length" class="host-empty"><strong>{{ hostFilter === 'pending' ? '暂无待批准电脑' : hostFilter === 'approved' ? '暂无已批准电脑' : hostFilter === 'all' ? '暂无已登记电脑' : '暂无当前接入的电脑' }}</strong></div>
        <div v-else class="host-list">
          <div class="host-column-labels" aria-hidden="true"><span>电脑</span><span>状态</span><span>操作</span></div>
          <article v-for="host in visibleHosts" :key="host.id" class="host-card">
            <div class="host-row">
              <div class="host-name"><h3>{{ host.name }}</h3><p v-if="host.approval_status === 'pending'" class="host-machine-id">机器标识：<code>{{ host.machine_id }}</code></p><span v-else class="host-connection">{{ host.online ? '在线' : '离线' }} · Agent {{ host.agent_version || '?' }}</span></div>
              <div class="host-row-state"><span class="host-badge" :class="tone(host.state)"><i aria-hidden="true"></i>{{ host.state === 'non_dispatch' ? '未就绪' : hostStateLabels[host.state] || host.state }}</span><p v-if="hostHint(host)" class="host-card-hint">{{ hostHint(host) }}</p></div>
              <div class="host-card-actions host-row-actions">
                <template v-if="host.approval_status === 'pending'">
                  <button class="host-button primary" type="button" :disabled="Boolean(busy)" @click="hostAction(host, 'approve')">{{ busy === `host:${host.id}` ? '处理中…' : '批准接入' }}</button>
                  <button class="host-button" type="button" :disabled="Boolean(busy)" @click="confirmation = { host, action: 'reject' }">拒绝</button>
                </template>
                <template v-else-if="host.approval_status === 'approved'">
                  <button v-if="isSuperAdmin && host.agent_update_available" class="host-button" type="button" :title="upgradeReason(host)" :aria-label="`${host.name}：${upgradeReason(host)}`" :disabled="Boolean(busy) || !host.remote_upgrade_supported || !host.online || Boolean(host.active_run_id) || host.upgrade_pending" @click="upgradeHost(host)">{{ busy === `upgrade:${host.id}` ? '提交中…' : '升级' }}</button>
                  <button v-if="host.active_run_id" class="host-button" type="button" @click="showRun(host.active_run_id, $event)">查看运行 #{{ host.active_run_id }}</button>
                  <button class="host-button" type="button" :disabled="Boolean(busy) || Boolean(host.active_run_id)" @click="setMode(host)">{{ busy === `host:${host.id}` ? '处理中…' : host.dispatch_enabled ? '关闭主控调度' : '开启主控调度' }}</button>
                </template>
                <button class="host-button quiet" type="button" :aria-expanded="expandedHosts.has(host.id)" :aria-controls="`host-machine-${host.id}`" :aria-label="`${host.name}：${expandedHosts.has(host.id) ? '收起' : '查看'}详情`" @click="toggleHostDetails(host.id)">{{ expandedHosts.has(host.id) ? '收起' : '详情' }}</button>
              </div>
            </div>
            <p v-if="host.approval_status === 'approved' && host.agent_update_available && host.remote_upgrade_supported === false" class="host-help">首次需在目标电脑更新到 0.5.0，之后可在此升级。</p>
            <div v-if="host.agent_update" class="host-update-status" aria-live="polite">
              <details v-if="['failed', 'uncertain'].includes(host.agent_update.status)"><summary>{{ upgradeLabel(host.agent_update) }}</summary><p>{{ host.agent_update.message }}</p></details>
              <span v-else>{{ upgradeLabel(host.agent_update) }}</span>
            </div>
            <p v-if="host.active_run_id && host.approval_status === 'approved'" class="host-help">更改调度模式前，请先停止当前运行并等待电脑确认。</p>
            <div v-show="expandedHosts.has(host.id)" :id="`host-machine-${host.id}`" class="host-machine-info">
              <dl class="host-facts">
                <div><dt>连接</dt><dd>{{ host.online ? '在线' : '离线' }}</dd></div>
                <div><dt>主控调度</dt><dd>{{ host.dispatch_enabled ? '已开启' : '已关闭' }}</dd></div>
                <div><dt>本机调度</dt><dd>{{ host.agent_dispatch_enabled ? '已开启' : '已关闭' }}</dd></div>
                <div><dt>桌面会话</dt><dd>{{ host.interactive_session ? '可用' : '不可用' }}</dd></div>
              </dl>
              <div class="host-machine-meta"><div><p>#{{ host.id }} · Agent {{ host.agent_version || '版本未知' }}</p><p v-if="host.approval_status !== 'pending'">机器标识：<code>{{ host.machine_id }}</code></p><p>最近心跳 {{ formatTime(host.last_seen_at) }}</p></div><button v-if="host.approval_status === 'approved'" class="host-button danger" type="button" :disabled="Boolean(busy)" @click="confirmation = { host, action: 'revoke' }">撤销接入</button></div>
            </div>
            <div v-if="confirmation?.host.id === host.id" class="host-confirm" @keydown.esc="confirmation = null">
              <p>{{ confirmation.action === 'revoke' ? `撤销「${host.name}」后，它将无法继续连接主控。` : `拒绝「${host.name}」的接入申请？` }}</p>
              <div class="host-card-actions"><button class="host-button" type="button" :disabled="Boolean(busy)" @click="confirmation = null">取消</button><button class="host-button danger" type="button" :disabled="Boolean(busy)" @click="confirmAction">{{ busy ? '处理中…' : confirmation.action === 'revoke' ? '确认撤销' : '确认拒绝' }}</button></div>
            </div>
          </article>
        </div>
      </section>

      <section class="host-panel" aria-labelledby="remote-runs-heading">
        <header class="host-section-heading"><div><h3 id="remote-runs-heading">远程运行记录</h3><p>最近 {{ runs.length }} 条，最多 100 条</p></div><div class="host-filter"><select id="run-filter" v-model="runFilter" aria-label="远程运行状态"><option value="all">全部状态</option><option value="active">尚未结束</option><option value="attention">失败或待确认</option></select></div></header>
        <div v-if="!visibleRuns.length" class="host-empty"><strong>{{ runs.length ? '没有符合条件的运行' : '暂无远程运行' }}</strong></div>
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
          <p v-if="runDetail.stop_requested && activeRunStates.has(runDetail.status)" class="host-inline-warning">已请求停止，等待宿主机确认进程结束。</p>
          <p v-if="runDetail.status === 'uncertain'" class="host-inline-warning">旧运行尚未确认结束，宿主机暂不接收新任务。</p>
          <p v-if="runDetail.waiting_reason" class="host-help">{{ runDetail.waiting_reason }}</p>
          <p v-if="runDetail.error" class="host-feedback" :class="runDetail.status === 'succeeded' ? 'success' : ['failed', 'timed_out'].includes(runDetail.status) ? 'error' : ''">{{ runDetail.error }}</p>
          <div v-if="runDetail.collection_progress" class="host-progress">
            <strong>采集进度</strong>
            <p>{{ runDetail.collection_progress.latest.stage || '任务上报' }}<template v-if="runDetail.collection_progress.latest.page != null"> · 第 {{ runDetail.collection_progress.latest.page }} 页</template><template v-if="runDetail.collection_progress.latest.collected != null"> · 已采集 {{ runDetail.collection_progress.latest.collected }} 条</template><template v-if="runDetail.collection_progress.latest.total != null"> / {{ runDetail.collection_progress.latest.total }} 条</template></p>
            <small>{{ runDetail.collection_progress.latest.message || `已收到 ${runDetail.collection_progress.event_count} 条结构化进度` }}</small>
          </div>
          <div v-if="runDetail.maintenance" class="host-maintenance" :class="{ ready: runDetail.maintenance.status === 'candidate' }">
            <div><strong>AI 维护建议</strong><p v-if="['pending','generating'].includes(runDetail.maintenance.status)">正根据本次代码和失败日志生成建议，生成后需管理员确认。</p><p v-else-if="runDetail.maintenance.status === 'candidate'">候选 v{{ runDetail.maintenance.sequence }} 尚未试跑。确认后会启用，并在原宿主机新建运行。</p><p v-else-if="runDetail.maintenance.status === 'activated'">管理员已确认候选，远程重跑 #{{ runDetail.maintenance.rerun_remote_run_id }} 已单独记录。</p><p v-else>{{ runDetail.maintenance.note || '本次未生成代码候选。' }}</p></div>
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
          <EnvironmentDetails :environment="runDetail.environment" :status="runDetail.environment_status" />
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
.host-section-heading, .host-detail-summary { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-width: 0; }
.host-section-heading p { margin-top: 4px; color: var(--host-muted); font-size: 12px; }
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
.host-feedback { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; padding: 12px 16px; border: 1px solid var(--host-border); border-radius: 8px; overflow-wrap: anywhere; }
.host-feedback.success { background: #edf8f0; border-color: #bddfc9; color: #176b35; }
.host-feedback.error { background: #fff4f0; border-color: #efd0c6; color: #a53721; }
.host-panel { min-width: 0; padding: 20px; border: 1px solid var(--host-border); border-radius: 12px; background: #fff; }
.host-section-heading { margin-bottom: 16px; align-items: flex-start; }
.host-list-heading { align-items: center; margin-bottom: 8px; }
.host-list-title { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.host-list-title h2 { font-size: 16px; line-height: 24px; }
.host-list-title > span, .host-connection { color: var(--host-muted); font-size: 12px; }
.host-toolbar { display: flex; align-items: center; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.hosts-page .host-toolbar select { width: auto; min-width: 124px; height: 36px; }
.host-summary { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 20px; padding-bottom: 16px; color: var(--host-muted); font-size: 12px; }
.host-summary strong { margin-left: 3px; color: var(--host-text); font-weight: 500; font-variant-numeric: tabular-nums; }
.host-connect { display: grid; gap: 12px; margin-bottom: 16px; padding: 16px; border-radius: 8px; background: var(--host-soft); }
.host-connect .host-guide { margin-top: 0; }
.host-guide { margin-top: 16px; color: var(--host-muted); font-size: 12px; }
.host-guide summary { width: fit-content; cursor: pointer; }
.host-steps { display: grid; gap: 8px; margin: 12px 0 0; padding-left: 20px; }
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
.host-inline-warning { padding: 10px 12px; border: 1px solid #e7dca8; border-radius: 8px; background: #fffbeb; color: #715912; font-size: 12px; overflow-wrap: anywhere; }
.host-filter { display: flex; flex: 0 0 auto; align-items: center; gap: 8px; }
.host-filter select { width: auto; min-width: 110px; }
.host-list { display: grid; }
.host-card { min-width: 0; padding: 16px 0; }
.host-card:last-child { padding-bottom: 0; }
.host-card + .host-card { border-top: 1px solid var(--host-border); }
.host-row, .host-column-labels { display: grid; grid-template-columns: minmax(160px, 1fr) minmax(150px, .85fr) minmax(240px, 300px); align-items: center; gap: 20px; }
.host-column-labels { padding: 10px 0; border-top: 1px solid var(--host-border); border-bottom: 1px solid var(--host-border); color: var(--host-muted); font-size: 12px; }
.host-column-labels > :last-child { text-align: right; }
.host-name h3 { font-size: 14px; line-height: 22px; overflow-wrap: anywhere; }
.host-connection { display: block; margin-top: 3px; }
.host-row-state { display: grid; justify-items: start; gap: 4px; min-width: 0; }
.host-row-actions { justify-content: flex-end; }
.host-name { min-width: 0; }
.host-badge { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 5px; padding: 3px 7px; border-radius: 6px; background: #f0f2ef; color: #5d655d; font-size: 11px; line-height: 18px; white-space: nowrap; }
.host-badge i { width: 6px; height: 6px; flex: 0 0 auto; border-radius: 50%; background: currentColor; }
.host-badge.good { background: #eaf6ed; color: #167536; }
.host-badge.danger { background: #fff0ea; color: #a53721; }
.host-badge.warning { background: #fcf6db; color: #775e16; }
.host-badge.info { background: #eaf2f4; color: #365f6b; }
.host-machine-id { margin-top: 8px !important; color: var(--host-muted); font-size: 11px; }
.host-card-hint { color: var(--host-muted); font-size: 12px; overflow-wrap: anywhere; }
.host-facts, .host-run-facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.host-facts div, .host-run-facts div { min-width: 0; }
.host-facts dt, .host-run-facts dt { color: var(--host-muted); font-size: 11px; }
.host-facts dd, .host-run-facts dd { margin: 4px 0 0; font-size: 12px; overflow-wrap: anywhere; }
.host-card-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.host-card > .host-help { margin-top: 8px; }
.host-update-status { margin-top: 8px; font-size: 12px; color: var(--host-muted); overflow-wrap: anywhere; }
.host-update-status summary { width: fit-content; cursor: pointer; color: #a53721; }
.host-update-status details p { margin-top: 8px; }
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
.host-technical { margin-top: 16px; padding-top: 12px; border-top: 1px solid #ecefec; }
.host-technical summary { cursor: pointer; font-size: 12px; }
.host-technical p { margin: 8px 0; color: var(--host-muted); font-size: 12px; }
.host-technical code { font-size: 11px; }
.host-machine-info { margin-top: 16px; padding-top: 16px; border-top: 1px solid #ecefec; }
.host-machine-meta { display: flex; justify-content: space-between; align-items: flex-end; flex-wrap: wrap; gap: 12px; margin-top: 16px; }
.host-machine-meta p { margin-top: 4px; color: var(--host-muted); font-size: 12px; overflow-wrap: anywhere; }
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
@media (max-width: 1000px) { .host-column-labels { display: none; } .host-row { grid-template-columns: minmax(160px, 1fr) minmax(140px, .8fr); gap: 12px; } .host-row-actions { grid-column: 1 / -1; justify-content: flex-start; } .host-list { border-top: 1px solid var(--host-border); } }
@media (max-width: 720px) { .host-panel { padding: 16px; } .host-section-heading { flex-wrap: wrap; } .host-toolbar { justify-content: flex-start; } .host-row { grid-template-columns: minmax(0, 1fr); gap: 8px; } .host-row-state { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 8px; } .host-row-actions { gap: 8px; } .host-facts, .host-run-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); } .host-runs > li { align-items: flex-start; flex-direction: column; padding: 12px 8px; } .host-maintenance { align-items: flex-start; flex-direction: column; } .host-maintenance-actions { width: 100%; justify-content: flex-start; } .host-run-summary + .host-card-actions { align-self: stretch; justify-content: space-between; } }
@media (max-width: 400px) { .host-panel { padding: 12px; } .host-toolbar { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; width: 100%; gap: 6px; } .hosts-page .host-toolbar select { width: 100%; min-width: 0; padding: 0 6px; } .host-summary { gap: 6px 12px; } .host-summary > span:first-child { display: none; } .host-badge { padding: 3px 5px; } .host-button { padding: 7px 10px; } .host-enrollment, .host-connect { padding: 12px; } .host-detail-summary { flex-wrap: wrap; } }
@media (prefers-reduced-motion: reduce) { .host-button { transition: none; } .host-button:active:not(:disabled) { transform: none; } }
</style>
