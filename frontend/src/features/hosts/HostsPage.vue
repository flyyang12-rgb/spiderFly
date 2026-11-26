<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { formatFileSize, formatTime } from '../../lib/format'
import { useWorkspaceContext } from '../../workspace/context'
import { activeRunStates, hostStateLabels, runStatusLabels, useHosts } from './useHosts'
import EnvironmentDetails from './EnvironmentDetails.vue'
import WorkflowDialog from '../workflows/WorkflowDialog.vue'

const { handleSessionExpired, isSuperAdmin } = useWorkspaceContext()
const props = defineProps({ openRunId: { type: Number, default: null } })
const emit = defineEmits(['run-opened'])
const {
  hosts, viewer, runs, loading, refreshing, loadError, actionError, notice, busy, enrollment,
  selectedRunId, runDetail, detailLoading, detailError,
  refresh, createEnrollment, hostAction, deleteHost, upgradeHost, setMode, stopRun,
  activateCandidate, dismissCandidate,
  openRun, closeRun, loadRunDetail,
} = useHosts({ onSessionExpired: handleSessionExpired })

const confirmation = ref(null)
const deletingHost = ref(null)

async function confirmDeleteHost() {
  if (!deletingHost.value || busy.value) return
  const host = deletingHost.value
  if (await deleteHost(host)) {
    expandedHosts.value.delete(host.id)
    deletingHost.value = null
  }
}
const codeInput = ref(null)
const runDrawer = ref(null)
const connectOpen = ref(false)
const finderOpen = ref(false)
const installerName = '安装并接入Agent.bat'
const installerSearch = 'search-ms:query=' + encodeURIComponent(`System.FileName:="${installerName}"`) + '&crumb=location:shell%3AMyComputerFolder'
const folderPicker = ref(null)
const foundPaths = ref(null)
function findInstallerFiles(event) {
  const input = event.target
  foundPaths.value = Array.from(input.files)
    .filter(file => file.name.toLowerCase() === installerName.toLowerCase() || /^SpiderFlyAgent(?:[- .(（].*)?\.zip$/i.test(file.name))
    .map(file => file.webkitRelativePath)
    .sort()
  input.value = ''
}
const expandedHosts = ref(new Set())
const hostFilter = ref('current')
const runFilter = ref('all')
const statusFilter = ref('all')
const hostQuery = ref('')
const statusOptions = [
  { value: 'all', label: '全部' }, { value: 'idle', label: '空闲' },
  { value: 'running', label: '运行中' }, { value: 'offline', label: '离线' },
]
const scopedHosts = computed(() => {
  const query = hostQuery.value.trim().toLowerCase()
  return hosts.value.filter(host => {
    const inScope = hostFilter.value === 'all' || (hostFilter.value === 'current'
      ? ['pending', 'approved'].includes(host.approval_status) : host.approval_status === hostFilter.value)
    return inScope && (!query || host.name.toLowerCase().includes(query))
  })
})
function matchesStatus(host, status) {
  return status === 'all' || host.state === status || (status === 'running' && host.state === 'stopping')
}
const statusCounts = computed(() => Object.fromEntries(statusOptions.map(({ value }) =>
  [value, scopedHosts.value.filter(host => matchesStatus(host, value)).length])))
const visibleHosts = computed(() => scopedHosts.value.filter(host => matchesStatus(host, statusFilter.value)))
const currentHost = computed(() => viewer.value?.kind === 'agent' ? hosts.value.find(host => host.id === viewer.value.host_id) : null)
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
  if (host.state === 'stopping') return '正在停止'
  if (host.state !== 'non_dispatch') return ''
  if (!host.dispatch_enabled) return '主控调度已关闭'
  if (!host.agent_dispatch_enabled) return '本机调度已关闭'
  if (!host.interactive_session) return '桌面会话不可用'
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

function showRun(id) {
  openRun(id)
}

watch([() => props.openRunId, loading], ([id, isLoading]) => {
  if (!id || isLoading) return
  showRun(id)
  emit('run-opened')
}, { immediate: true })

function closeDetails() {
  closeRun()
}

watch(selectedRunId, async (id, previous, onCleanup) => {
  if (id == null) return
  const trigger = document.activeElement
  const app = document.getElementById('app')
  const previousInert = app?.inert
  const previousOverflow = document.body.style.overflow
  if (app) app.inert = true
  document.body.style.overflow = 'hidden'
  document.addEventListener('keydown', drawerKeyboard)
  onCleanup(() => {
    document.removeEventListener('keydown', drawerKeyboard)
    if (app) app.inert = previousInert
    document.body.style.overflow = previousOverflow
    if (trigger?.isConnected) trigger.focus({ preventScroll: true })
  })
  await nextTick()
  if (selectedRunId.value === id) runDrawer.value?.focus({ preventScroll: true })
}, { immediate: true })

function drawerKeyboard(event) {
  if (event.key === 'Escape') {
    event.stopPropagation()
    closeDetails()
  }
  if (event.key !== 'Tab' || !runDrawer.value) return
  const controls = [...runDrawer.value.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]')]
    .filter(element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden')
  const first = controls[0]
  const last = controls.at(-1)
  if (!first) {
    event.preventDefault()
    runDrawer.value.focus({ preventScroll: true })
  } else if (event.shiftKey && (document.activeElement === first || document.activeElement === runDrawer.value || !runDrawer.value.contains(document.activeElement))) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && (document.activeElement === last || !runDrawer.value.contains(document.activeElement))) {
    event.preventDefault()
    first.focus()
  }
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
        <h2 id="host-list-heading" class="sr-only">宿主机列表</h2>
        <div class="host-status-bar" role="group" aria-label="宿主机状态">
          <button v-for="option in statusOptions" :key="option.value" type="button" :aria-pressed="statusFilter === option.value" @click="statusFilter = option.value">
            {{ option.label }} <span>{{ statusCounts[option.value] }}</span>
          </button>
        </div>
        <div class="machine-tools">
          <div class="machine-search">
            <input v-model="hostQuery" type="search" aria-label="搜索电脑名称" placeholder="搜索电脑名称" />
            <select id="host-filter" v-model="hostFilter" aria-label="接入范围"><option value="current">当前接入</option><option value="pending">待批准</option><option value="approved">已批准</option><option value="all">全部登记</option></select>
          </div>
          <div class="host-toolbar">
            <button class="host-button" type="button" :disabled="refreshing" @click="refresh()">{{ refreshing ? '刷新中…' : '刷新' }}</button>
            <button class="host-button" type="button" :aria-expanded="finderOpen" aria-controls="agent-finder" @click="finderOpen = !finderOpen">查找接入程序</button>
            <button class="host-button" :class="{ primary: !connectOpen }" type="button" :aria-expanded="connectOpen" aria-controls="host-enrollment-panel" @click="connectOpen = !connectOpen">{{ connectOpen ? '收起接入' : '接入新电脑' }}</button>
          </div>
        </div>
        <div class="host-summary">
          <span v-if="viewer?.kind === 'controller'" class="host-viewer">当前电脑 · 主控本机</span>
          <span v-else-if="currentHost && !visibleHosts.includes(currentHost)">当前电脑 · {{ currentHost.name }}（不在当前筛选内）</span>
          <span v-else-if="viewer?.kind === 'shared'">多台电脑共用连接地址，无法识别当前电脑</span>
          <span v-else-if="viewer?.kind === 'unknown'" title="请确认这台电脑的 Agent 在线，且浏览器直连同一主控。">未识别到当前电脑</span>
        </div>

        <section v-if="finderOpen" id="agent-finder" class="host-connect" aria-label="查找接入程序">
          <div class="host-card-actions"><a class="host-button" :href="installerSearch">在 Windows 中查找</a><button type="button" class="host-button" @click="folderPicker.click()">在选定文件夹中查找</button><code>{{ installerName }}</code></div>
          <input ref="folderPicker" type="file" webkitdirectory multiple hidden @change="findInstallerFiles" />
          <div v-if="foundPaths" aria-live="polite" class="host-help">
            <p>{{ foundPaths.length ? '所选文件夹中找到以下文件：' : '这个文件夹里没有找到接入程序或安装包。' }}</p>
            <ul v-if="foundPaths.length" class="host-found-paths"><li v-for="path in foundPaths" :key="path"><code>{{ path }}</code></li></ul>
          </div>
          <p class="host-help">找到文件后可以打开它所在的文件夹。若搜索窗口没有打开，按 Win + E，选择“此电脑”，在右上角搜索上面的文件名。</p>
          <p class="host-help">如果还没解压，按 Ctrl + J，在下载记录里找到 SpiderFlyAgent 压缩包并打开所在文件夹。<a href="/api/hosts/agent-download" download>重新下载安装包</a></p>
        </section>

        <section v-show="connectOpen" id="host-enrollment-panel" class="host-connect" aria-label="接入新电脑">
          <div class="host-card-actions">
            <button class="host-button primary" type="button" :disabled="Boolean(busy)" @click="createEnrollment">{{ busy === 'enrollment' ? '正在生成…' : '生成接入码' }}</button>
            <a class="host-button" href="/api/hosts/agent-download" download>下载 Agent 安装包</a>
          </div>
          <details class="host-guide">
            <summary>接入步骤</summary>
            <ol class="host-steps">
              <li>在另一台已登录 Windows 桌面的电脑上，解压 Agent，双击“安装并接入Agent”，填写主控地址、接入码和电脑名称。</li>
              <li>展开这台电脑的“详情”，核对名称与机器标识后点击“批准接入”。</li>
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

        <div v-if="!visibleHosts.length" class="host-empty" role="status"><strong>{{ hosts.length ? '没有符合条件的电脑' : '暂无已登记电脑' }}</strong></div>
        <div v-else class="host-list">
          <article v-for="host in visibleHosts" :key="host.id" class="host-card" :class="{ expanded: expandedHosts.has(host.id) }">
            <div class="machine-heading">
              <div class="host-name-line"><h3>{{ host.name }}</h3><span v-if="host.id === currentHost?.id" class="host-viewer">当前电脑</span></div>
              <span class="host-badge" :class="tone(host.state)"><i aria-hidden="true"></i>{{ host.state === 'idle' ? '空闲' : host.state === 'stopping' ? '运行中' : hostStateLabels[host.state] || host.state }}</span>
            </div>
            <dl class="machine-stats">
              <div><dt>当前任务</dt><dd><button v-if="host.active_run_id" class="host-task-link" type="button" @click="showRun(host.active_run_id)">{{ host.active_task_name || '运行 #' + host.active_run_id }}</button><template v-else>—</template></dd></div>
              <div><dt>Agent 版本</dt><dd>{{ host.agent_version || '未知' }}</dd></div>
              <div><dt>最近心跳</dt><dd>{{ host.last_seen_at ? formatTime(host.last_seen_at) : '—' }}</dd></div>
            </dl>
            <div class="machine-footer">
              <span class="host-card-hint">{{ hostHint(host) || (host.state === 'offline' ? '' : host.online ? '在线' : '离线') }}</span>
              <button class="host-button quiet host-details-toggle" type="button" :aria-expanded="expandedHosts.has(host.id)" :aria-controls="`host-machine-${host.id}`" :aria-label="`${host.name}：${expandedHosts.has(host.id) ? '收起' : '查看'}详情`" @click="toggleHostDetails(host.id)">{{ expandedHosts.has(host.id) ? '收起' : '详情' }}</button>
            </div>
            <div v-show="expandedHosts.has(host.id)" :id="`host-machine-${host.id}`" class="host-machine-info">
              <dl class="host-facts">
                <div><dt>主控调度</dt><dd>{{ host.dispatch_enabled ? '已开启' : '已关闭' }}</dd></div>
                <div><dt>本机调度</dt><dd>{{ host.agent_dispatch_enabled ? '已开启' : '已关闭' }}</dd></div>
                <div><dt>桌面会话</dt><dd>{{ host.interactive_session ? '可用' : '不可用' }}</dd></div>
              </dl>
              <div class="host-machine-meta">
                <div><p>电脑 #{{ host.id }} · 机器标识：<code>{{ host.machine_id }}</code></p></div>
                <div class="host-card-actions">
                  <template v-if="host.approval_status === 'pending'">
                    <button class="host-button primary" type="button" :disabled="Boolean(busy)" @click="hostAction(host, 'approve')">{{ busy === `host:${host.id}` ? '处理中…' : '批准接入' }}</button>
                    <button class="host-button" type="button" :disabled="Boolean(busy)" @click="confirmation = { host, action: 'reject' }">拒绝</button>
                  </template>
                  <template v-else-if="host.approval_status === 'approved'">
                    <button v-if="isSuperAdmin && host.agent_update_available" class="host-button" type="button" :title="upgradeReason(host)" :aria-label="`${host.name}：${upgradeReason(host)}`" :disabled="Boolean(busy) || !host.remote_upgrade_supported || !host.online || Boolean(host.active_run_id) || host.upgrade_pending" @click="upgradeHost(host)">{{ busy === `upgrade:${host.id}` ? '提交中…' : '升级' }}</button>
                    <button class="host-button" type="button" :disabled="Boolean(busy) || Boolean(host.active_run_id)" @click="setMode(host)">{{ busy === `host:${host.id}` ? '处理中…' : host.dispatch_enabled ? '关闭主控调度' : '开启主控调度' }}</button>
                    <button class="host-button danger" type="button" :disabled="Boolean(busy)" @click="confirmation = { host, action: 'revoke' }">撤销接入</button>
                  </template>
                  <button v-if="host.approval_status === 'revoked'" class="host-button danger" type="button" :disabled="Boolean(busy)" @click="actionError = ''; deletingHost = host">永久删除</button>
                </div>
              </div>
              <p v-if="host.environment_reporting_supported === false" class="host-help">Agent 0.4.0 起可回传业务环境和依赖版本。</p>
              <p v-if="host.approval_status === 'approved' && host.agent_update_available && host.remote_upgrade_supported === false" class="host-help">首次需在目标电脑更新到 0.5.0，之后可在此升级。</p>
              <div v-if="host.agent_update" class="host-update-status" aria-live="polite">
                <details v-if="['failed', 'uncertain'].includes(host.agent_update.status)"><summary>{{ upgradeLabel(host.agent_update) }}</summary><p>{{ host.agent_update.message }}</p></details>
                <span v-else>{{ upgradeLabel(host.agent_update) }}</span>
              </div>
              <p v-if="host.active_run_id && host.approval_status === 'approved'" class="host-help">更改调度模式前，请先停止当前运行并等待电脑确认。</p>
              <div v-if="confirmation?.host.id === host.id" class="host-confirm" @keydown.esc="confirmation = null">
                <p>{{ confirmation.action === 'revoke' ? `撤销「${host.name}」后，它将无法继续连接主控。` : `拒绝「${host.name}」的接入申请？` }}</p>
                <div class="host-card-actions"><button class="host-button" type="button" :disabled="Boolean(busy)" @click="confirmation = null">取消</button><button class="host-button danger" type="button" :disabled="Boolean(busy)" @click="confirmAction">{{ busy ? '处理中…' : confirmation.action === 'revoke' ? '确认撤销' : '确认拒绝' }}</button></div>
              </div>
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
            <div class="host-card-actions"><span class="host-badge" :class="tone(run.status)"><i aria-hidden="true"></i>{{ runStatusLabels[run.status] || run.status }}</span><button class="host-button" type="button" :aria-label="`查看运行 #${run.id} ${run.task_name} 的日志与结果`" :aria-expanded="selectedRunId === run.id" aria-controls="remote-run-detail" @click="showRun(run.id)">日志与结果</button></div>
          </li>
        </ul>
      </section>

    </template>
    <Teleport to="body">
      <Transition name="run-drawer">
        <div v-if="selectedRunId != null" class="hosts-page management-workspace host-drawer-layer" @mousedown.self="closeDetails">
          <section id="remote-run-detail" ref="runDrawer" class="host-drawer" role="dialog" aria-modal="true" aria-labelledby="run-detail-heading" tabindex="-1">
            <header class="run-drawer-heading">
              <div class="run-drawer-title"><h2 id="run-detail-heading">{{ runDetail?.task_name || '日志与结果' }}</h2><span>运行 #{{ selectedRunId }}<template v-if="runDetail?.version_sequence != null"> · v{{ runDetail.version_sequence }}</template></span></div>
              <div class="host-card-actions">
                <button class="host-button" type="button" :disabled="detailLoading" @click="loadRunDetail(selectedRunId)">{{ detailLoading ? '刷新中…' : '刷新' }}</button>
                <button class="run-drawer-close" type="button" aria-label="关闭日志与结果" @click="closeDetails">×</button>
              </div>
            </header>
            <div class="run-drawer-body">
              <p v-if="actionError" class="host-feedback error" role="alert">{{ actionError }}</p>
              <p v-if="notice" class="host-feedback success" role="status">{{ notice }}</p>
              <p v-if="detailLoading" class="host-help" role="status">正在载入运行详情…</p>
              <div v-if="detailError" class="host-feedback error" role="alert"><span>运行详情刷新失败：{{ detailError }}</span><button class="host-button" type="button" @click="loadRunDetail(selectedRunId)">重试</button></div>
              <div v-if="runDetail" class="run-detail-layout">
                <aside class="run-context" aria-label="执行详情">
                  <div class="host-detail-summary"><div><h3>执行详情</h3><p>{{ runDetail.host_name }} · <span class="host-badge" :class="tone(runDetail.status)"><i aria-hidden="true"></i>{{ runStatusLabels[runDetail.status] || runDetail.status }}</span></p></div><button v-if="activeRunStates.has(runDetail.status) && !runDetail.stop_requested" class="host-button danger" type="button" :disabled="Boolean(busy)" @click="stopRun(runDetail)">{{ busy === `stop:${runDetail.id}` ? '正在请求…' : runDetail.status === 'queued' ? '取消排队' : '请求停止' }}</button></div>
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
                  <details class="host-technical"><summary>执行快照</summary><p v-if="runDetail.project_entry">项目入口 {{ runDetail.project_entry }} · 项目版本 {{ runDetail.project_digest.slice(0, 12) }}</p><p v-else>本次运行固定使用 v{{ runDetail.version_sequence ?? '?' }}（内部版本 #{{ runDetail.code_version_id ?? '—' }}），代码与依赖内容不可变。</p><code>{{ runDetail.package_hash }}</code></details>
                  <EnvironmentDetails :environment="runDetail.environment" :status="runDetail.environment_status" />
                </aside>
                <section class="run-output" aria-label="日志与结果">
                  <h3>运行日志</h3>
                  <p v-if="runDetail.events_truncated" class="host-help">仅显示最近 500 条日志，完整事件仍保存在主控。</p>
                  <div v-if="runDetail.events?.length" class="host-log" tabindex="0" role="region" aria-label="运行原始日志，可使用键盘滚动">
                    <div v-for="event in runDetail.events" :key="event.seq" class="host-log-entry" :class="{ 'log-error': event.kind === 'stderr' }"><span class="host-log-label">#{{ event.seq }} {{ eventLabel(event) }}{{ event.status ? ' · ' + (runStatusLabels[event.status] || event.status) : '' }}</span><pre v-if="event.text">{{ event.text }}</pre></div>
                  </div>
                  <p v-else class="host-help">{{ runDetail.status === 'queued' ? '任务尚未开始，等待目标电脑领取。' : '执行端尚未回传日志。' }}</p>
                  <h4 class="host-subheading">结果文件</h4>
                  <ul v-if="runDetail.artifacts?.length" class="host-artifacts"><li v-for="artifact in runDetail.artifacts" :key="artifact.name"><div><strong>{{ artifact.name }}</strong><span>{{ formatFileSize(artifact.size) }}</span></div><a v-if="artifactUrl(artifact.download_url)" class="host-button" :href="artifactUrl(artifact.download_url)" :aria-label="`下载 ${artifact.name}`" download>下载</a><span v-else class="host-help">下载地址不可用</span></li></ul>
                  <p v-else class="host-help">暂未收到结果文件。脚本需要把结果写入本次产物目录。</p>
                </section>
              </div>
            </div>
          </section>
        </div>
      </Transition>
    </Teleport>
    <WorkflowDialog v-if="deletingHost" title="永久删除电脑" :close-disabled="Boolean(busy)" @close="deletingHost = null">
      <p>删除「{{ deletingHost.name }}」的登记和接入凭据，保留运行记录。再次使用需重新接入。</p>
      <p v-if="actionError" class="danger-text" role="alert">{{ actionError }}</p>
      <template #footer><button class="button secondary" type="button" :disabled="Boolean(busy)" @click="deletingHost = null">取消</button><button class="button danger" type="button" :disabled="Boolean(busy)" @click="confirmDeleteHost">{{ busy ? '删除中…' : '确认永久删除' }}</button></template>
    </WorkflowDialog>
  </section>
</template>

<style scoped>
.hosts-page { --host-green: #009139; --host-ink: var(--ink); --host-text: #292b28; --host-muted: var(--muted); --host-border: #dfe3df; --host-soft: #f7f8f6; display: grid; width: min(var(--content-width), 100%); min-width: 0; margin: 0 auto; gap: 16px; color: var(--host-text); font-size: 13px; line-height: 1.55; }
.hosts-page *, .hosts-page *::before, .hosts-page *::after { box-sizing: border-box; }
.hosts-page h2, .hosts-page h3, .hosts-page h4, .hosts-page p, .hosts-page dl { margin: 0; }
.hosts-page h2 { color: var(--host-ink); font-size: 20px; line-height: 28px; }
.hosts-page h3 { color: var(--host-ink); font-size: 15px; line-height: 22px; }
.hosts-page h4 { color: var(--host-ink); font-size: 14px; line-height: 20px; }
.hosts-page code { font-family: 'SFMono-Regular', Consolas, monospace; overflow-wrap: anywhere; }
.host-section-heading, .host-detail-summary { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-width: 0; }
.host-section-heading p { margin-top: 4px; color: var(--host-muted); font-size: 12px; }
.host-button { display: inline-flex; flex: 0 0 auto; align-items: center; justify-content: center; gap: 7px; min-height: 38px; padding: 8px 13px; border: 1px solid; border-radius: 8px; font-family: inherit; font-size: 13px; font-weight: 500; line-height: 20px; text-decoration: none; white-space: nowrap; cursor: pointer; transition: background-color 140ms, border-color 140ms, transform 140ms; }
.host-button:active:not(:disabled) { transform: translateY(1px); }
.host-button.primary { font-weight: 650; }
.host-button.quiet { border-color: transparent; background: transparent; color: var(--action-green); }
.host-button[aria-expanded="true"] { border-color: #9ebba7; background: #e6f1e9; color: #195d39; }
.host-button:disabled, .hosts-page input:disabled, .hosts-page select:disabled { opacity: .55; cursor: not-allowed; }
.hosts-page :is(button, a, input, select, summary, [tabindex]):focus-visible { outline: 2px solid var(--control-focus); outline-offset: 2px; }
.host-feedback { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; padding: 12px 16px; border: 1px solid var(--host-border); border-radius: 8px; overflow-wrap: anywhere; }
.host-feedback.success { background: #edf8f0; border-color: #bddfc9; color: #176b35; }
.host-feedback.error { background: #fff4f0; border-color: #efd0c6; color: #a53721; }
.host-panel { min-width: 0; padding: 20px; border: 1px solid var(--host-border); border-radius: 12px; background: #fff; }
.host-section-heading { margin-bottom: 16px; align-items: flex-start; }
.host-toolbar { display: flex; align-items: center; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.host-summary { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 20px; padding-bottom: 16px; color: var(--host-muted); font-size: 12px; }
.host-connect { display: grid; gap: 12px; margin-bottom: 16px; padding: 16px; border-radius: 8px; background: var(--host-soft); }
.host-connect .host-guide { margin-top: 0; }
.host-found-paths { margin: 4px 0 0; padding-left: 20px; max-height: 160px; overflow: auto; overflow-wrap: anywhere; }
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
.hosts-page select { border-color: var(--control-line); color: var(--control-ink); cursor: pointer; }
.hosts-page input:hover, .hosts-page select:hover { border-color: #b8c2b8; }
.hosts-page input:focus, .hosts-page select:focus { border-color: var(--host-green); }
.host-inline-warning { padding: 10px 12px; border: 1px solid #e7dca8; border-radius: 8px; background: #fffbeb; color: #715912; font-size: 12px; overflow-wrap: anywhere; }
.host-filter { display: flex; flex: 0 0 auto; align-items: center; gap: 8px; }
.host-filter select { width: auto; min-width: 110px; }
.host-status-bar { display: flex; gap: clamp(8px, 2.5vw, 32px); border-bottom: 1px solid var(--host-border); }
.host-status-bar button { display: flex; gap: 6px; align-items: center; padding: 6px 2px 13px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--host-muted); font: inherit; cursor: pointer; white-space: nowrap; }
.host-status-bar button[aria-pressed="true"] { color: var(--host-green); border-bottom-color: currentColor; font-weight: 650; }
.host-status-bar button span { font-size: 12px; font-variant-numeric: tabular-nums; }
.machine-tools, .machine-search, .machine-heading, .machine-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.machine-tools { flex-wrap: wrap; padding: 16px 0; }
.machine-search { flex-wrap: wrap; gap: 8px; }
.machine-search input { width: 196px; }
.machine-search select { width: auto; }
.host-summary:empty { display: none; }
.host-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 265px), 1fr)); align-items: stretch; gap: 14px; }
.host-card { display: flex; flex-direction: column; min-width: 0; border: 1px solid var(--host-border); border-radius: 10px; padding: 16px 16px 8px; }
.host-card.expanded { grid-column: 1 / -1; }
.machine-heading { align-items: flex-start; }
.machine-heading h3 { overflow-wrap: anywhere; }
.host-card .machine-stats { display: grid; gap: 8px; margin: 14px 0 12px; }
.machine-stats > div { display: grid; grid-template-columns: 65px minmax(0, 1fr); align-items: baseline; gap: 8px; }
.machine-stats dt, .machine-stats dd { font-size: 12px; margin: 0; }
.machine-stats dt { color: var(--host-muted); }
.machine-stats dd { overflow-wrap: anywhere; }
.machine-footer { margin-top: auto; border-top: 1px solid var(--host-border); padding-top: 6px; }
.machine-footer .host-button { margin-left: auto; min-height: 32px; padding: 3px 4px; }
.host-details-toggle::after { content: ''; width: 6px; height: 6px; border: solid currentColor; border-width: 0 1.5px 1.5px 0; transform: translateY(-2px) rotate(45deg); }
.host-details-toggle[aria-expanded="true"]::after { transform: translateY(2px) rotate(225deg); }
.host-task-link { max-width: 100%; padding: 0; border: 0; background: transparent; color: var(--host-green); font: inherit; font-size: 13px; text-align: left; overflow-wrap: anywhere; cursor: pointer; }
.host-task-link:hover { text-decoration: underline; }
.host-name-line { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 8px; }
.host-viewer { display: inline-flex; align-items: center; padding: 1px 7px; border: 1px solid #c4d8dc; border-radius: 6px; background: #eff6f7; color: #365c66; font-size: 12px; font-weight: 550; line-height: 18px; white-space: nowrap; }
.host-card-hint:empty { display: none; }
.host-badge { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 5px; padding: 2px 8px; border-radius: 6px; background: #edf0ed; color: #535f56; font-size: 12px; font-weight: 550; line-height: 18px; white-space: nowrap; }
.host-badge i { width: 6px; height: 6px; flex: 0 0 auto; border-radius: 50%; background: currentColor; }
.host-badge.good { background: #eaf6ed; color: #167536; }
.host-badge.danger { background: #fff0ea; color: #a53721; }
.host-badge.warning { background: #fcf6db; color: #775e16; }
.host-badge.info { background: #eaf2f4; color: #365f6b; }
.host-card-hint { color: var(--host-muted); font-size: 12px; overflow-wrap: anywhere; }
.host-facts, .host-run-facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.host-facts div, .host-run-facts div { min-width: 0; }
.host-facts dt, .host-run-facts dt { color: var(--host-muted); font-size: 11px; }
.host-facts dd, .host-run-facts dd { margin: 4px 0 0; font-size: 12px; overflow-wrap: anywhere; }
.host-card-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.host-machine-info > .host-help { margin-top: 8px; }
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
.run-context > .host-inline-warning, .run-context > .host-feedback { margin-bottom: 12px; }
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
@media (max-width: 720px) {
  .host-panel { padding: 16px; }
  .host-section-heading { flex-wrap: wrap; }
  .host-toolbar { justify-content: flex-start; }
  .host-button, .hosts-page select { min-height: 44px; }
  .host-facts, .host-run-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .host-runs > li { align-items: flex-start; flex-direction: column; padding: 12px 8px; }
  .host-maintenance { align-items: flex-start; flex-direction: column; }
  .host-maintenance-actions { width: 100%; justify-content: flex-start; }
  .host-run-summary + .host-card-actions { align-self: stretch; justify-content: space-between; }
}
@media (max-width: 540px) {
  .host-toolbar { justify-content: flex-start; gap: 6px; }
}
@media (max-width: 400px) {
  .host-panel { padding: 12px; }
  .host-summary { gap: 6px 12px; }
  .machine-search input { width: min(54vw, 196px); }
  .host-button { padding: 7px 10px; }
  .host-enrollment, .host-connect { padding: 12px; }
  .host-detail-summary { flex-wrap: wrap; }
}
@media (prefers-reduced-motion: reduce) { .host-button { transition: none; } .host-button:active:not(:disabled) { transform: none; } }

.hosts-page.host-drawer-layer { position: fixed; inset: 0; z-index: 105; display: flex; justify-content: flex-end; width: 100%; margin: 0; background: rgba(23, 38, 29, .32); }
.host-drawer { display: flex; flex-direction: column; width: min(1360px, calc(100% - 72px)); height: 100dvh; min-width: 0; background: var(--surface); box-shadow: -12px 0 48px rgba(22, 48, 31, .14); outline: none; }
.run-drawer-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 24px; border-bottom: 1px solid var(--host-border); }
.run-drawer-title { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px; min-width: 0; }
.run-drawer-title h2 { font-size: 17px; overflow-wrap: anywhere; }
.run-drawer-title > span { color: var(--host-muted); font-size: 12px; white-space: nowrap; }
.run-drawer-heading .host-card-actions { flex-shrink: 0; }
.run-drawer-close { display: grid; place-items: center; width: 36px; height: 36px; padding: 0; border: 0; border-radius: 6px; background: transparent; color: var(--host-text); font: inherit; font-size: 25px; cursor: pointer; }
.run-drawer-close:hover { background: var(--host-soft); }
.run-drawer-body { flex: 1; min-height: 0; padding: 24px; overflow-y: auto; overscroll-behavior: contain; }
.run-drawer-body > .host-feedback { margin-bottom: 16px; }
.run-detail-layout { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 24px; align-items: start; }
.run-context { min-width: 0; padding-right: 24px; border-right: 1px solid var(--host-border); }
.run-context .host-detail-summary { flex-direction: column; gap: 12px; }
.run-context .host-run-facts { grid-template-columns: 1fr; gap: 14px; }
.run-context .host-maintenance { flex-direction: column; align-items: stretch; padding: 12px; }
.run-context .host-maintenance-actions { justify-content: flex-start; }
.run-context .host-maintenance-actions .host-button { white-space: normal; }
.run-output { min-width: 0; }
.run-output > h3 { margin-bottom: 16px; }
.run-output .host-log { min-height: 240px; max-height: max(280px, calc(100dvh - 300px)); }
.run-drawer-enter-active, .run-drawer-leave-active { transition: background-color 200ms ease; }
.run-drawer-enter-active .host-drawer, .run-drawer-leave-active .host-drawer { transition: transform 200ms ease; }
.run-drawer-enter-from, .run-drawer-leave-to { background-color: transparent !important; }
.run-drawer-enter-from .host-drawer, .run-drawer-leave-to .host-drawer { transform: translateX(100%); }
@media (max-width: 800px) {
  .host-drawer { width: 100%; }
  .run-drawer-heading { padding: 12px 16px; }
  .run-drawer-title { display: grid; }
  .run-drawer-body { padding: 16px; }
  .run-detail-layout { grid-template-columns: minmax(0, 1fr); gap: 24px; }
  .run-output { order: -1; }
  .run-context { padding: 20px 0 0; border-right: 0; border-top: 1px solid var(--host-border); }
  .run-context .host-run-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .run-output .host-log { min-height: 180px; }
  .run-drawer-close { width: 44px; height: 44px; }
}
@media (prefers-reduced-motion: reduce) {
  .run-drawer-enter-active, .run-drawer-leave-active, .run-drawer-enter-active .host-drawer, .run-drawer-leave-active .host-drawer { transition: none; }
}
</style>
