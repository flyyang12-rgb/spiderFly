<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

const API = '/api'
const navItems = [
  { id: 'overview', label: '运行总览', mark: 'O' },
  { id: 'tasks', label: '调度中心', mark: 'T' },
  { id: 'executions', label: '执行记录', mark: 'L' },
  { id: 'settings', label: '通知设置', mark: 'S' },
]
const triggerOptions = [
  { value: 'manual', label: '手动触发' },
  { value: 'once', label: '单次定时' },
  { value: 'interval', label: '间隔执行' },
  { value: 'daily', label: '每日执行' },
  { value: 'weekly', label: '每周执行' },
]
const weekdayOptions = [
  { value: 1, label: '一' }, { value: 2, label: '二' }, { value: 3, label: '三' },
  { value: 4, label: '四' }, { value: 5, label: '五' }, { value: 6, label: '六' },
  { value: 7, label: '日' },
]

const view = ref('overview')
const loading = ref(true)
const tasks = ref([])
const executions = ref([])
const overview = ref({})
const settings = ref({})
const taskModalOpen = ref(false)
const editingTask = ref(null)
const detail = ref(null)
const deletingTask = ref(null)
const saving = ref(false)
const toast = reactive({ visible: false, type: 'success', title: '', message: '' })
const filters = reactive({ name: '', enabled: 'all', trigger_type: 'all', app_name: 'all' })
let toastTimer = 0
let pollTimer = 0

const taskForm = reactive({
  name: '', description: '', app_name: '', script_path: '', python_path: '',
  timeout_seconds: 0, enabled: true, notify_on_success: true, notify_on_failure: true,
  trigger_type: 'manual', once_at: '', interval_value: 30, interval_unit: 'minutes',
  daily_time: '09:00', weekly_days: [1], weekly_time: '09:00',
})

const pageTitle = computed(() => navItems.find((item) => item.id === view.value)?.label || 'SpiderFly')
const activeExecutions = computed(() => executions.value.filter((item) => ['pending', 'running'].includes(item.status)))
const recentExecutions = computed(() => executions.value.slice(0, 6))
const attentionTasks = computed(() => tasks.value.filter((item) => ['failed', 'timeout'].includes(item.last_status)))
const appOptions = computed(() => [...new Set(tasks.value.map((item) => item.app_name).filter(Boolean))].sort())
const filteredTasks = computed(() => tasks.value.filter((task) => {
  const nameMatch = !filters.name.trim() || task.name.toLowerCase().includes(filters.name.trim().toLowerCase())
  const enabledMatch = filters.enabled === 'all' || Boolean(task.enabled) === (filters.enabled === 'enabled')
  const triggerMatch = filters.trigger_type === 'all' || task.trigger_type === filters.trigger_type
  const appMatch = filters.app_name === 'all' || task.app_name === filters.app_name
  return nameMatch && enabledMatch && triggerMatch && appMatch
}))

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options,
  })
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try { message = (await response.json()).detail || message } catch {}
    throw new Error(message)
  }
  if (response.status === 204) return null
  return response.json()
}

function showToast(type, title, message = '') {
  Object.assign(toast, { visible: true, type, title, message })
  window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => { toast.visible = false }, 3000)
}

async function loadAll({ quiet = false } = {}) {
  if (!quiet) loading.value = true
  try {
    const [taskItems, executionItems, overviewData, settingsData] = await Promise.all([
      request('/tasks'), request('/executions?limit=100'), request('/overview'), request('/settings'),
    ])
    tasks.value = taskItems
    executions.value = executionItems
    overview.value = overviewData
    settings.value = settingsData
    if (detail.value) {
      const refreshed = executionItems.find((item) => item.id === detail.value.id)
      if (refreshed) detail.value = refreshed
    }
  } catch (error) {
    if (!quiet) showToast('error', '无法连接 SpiderFly', error.message)
  } finally { loading.value = false }
}

function resetForm() {
  Object.assign(taskForm, {
    name: '', description: '', app_name: '', script_path: '', python_path: '', timeout_seconds: 0,
    enabled: true, notify_on_success: true, notify_on_failure: true, trigger_type: 'manual',
    once_at: '', interval_value: 30, interval_unit: 'minutes', daily_time: '09:00',
    weekly_days: [1], weekly_time: '09:00',
  })
}

function openCreate() {
  editingTask.value = null
  resetForm()
  taskModalOpen.value = true
}

function openEdit(task) {
  editingTask.value = task
  const config = task.trigger_config || {}
  Object.assign(taskForm, {
    name: task.name, description: task.description || '', app_name: task.app_name || '',
    script_path: task.script_path, python_path: task.python_path || '', timeout_seconds: task.timeout_seconds || 0,
    enabled: Boolean(task.enabled), notify_on_success: Boolean(task.notify_on_success),
    notify_on_failure: Boolean(task.notify_on_failure), trigger_type: task.trigger_type || 'manual',
    once_at: config.run_at ? config.run_at.slice(0, 16) : '', interval_value: config.value || 30,
    interval_unit: config.unit || 'minutes', daily_time: config.time || '09:00',
    weekly_days: config.weekdays || [1], weekly_time: config.time || '09:00',
  })
  taskModalOpen.value = true
}

function triggerConfig() {
  if (taskForm.trigger_type === 'once') return { run_at: taskForm.once_at }
  if (taskForm.trigger_type === 'interval') return { value: Number(taskForm.interval_value), unit: taskForm.interval_unit }
  if (taskForm.trigger_type === 'daily') return { time: taskForm.daily_time }
  if (taskForm.trigger_type === 'weekly') return { weekdays: taskForm.weekly_days, time: taskForm.weekly_time }
  return {}
}

function taskPayload() {
  return {
    name: taskForm.name, description: taskForm.description, app_name: taskForm.app_name,
    script_path: taskForm.script_path, python_path: taskForm.python_path,
    timeout_seconds: Number(taskForm.timeout_seconds || 0), enabled: taskForm.enabled,
    notify_on_success: taskForm.notify_on_success, notify_on_failure: taskForm.notify_on_failure,
    trigger_type: taskForm.trigger_type, trigger_config: triggerConfig(),
  }
}

async function saveTask() {
  if (!taskForm.name.trim() || !taskForm.script_path.trim()) {
    showToast('error', '请完善任务信息', '任务名称和脚本路径不能为空')
    return
  }
  if (taskForm.trigger_type === 'once' && !taskForm.once_at) return showToast('error', '请选择单次执行时间')
  if (taskForm.trigger_type === 'weekly' && !taskForm.weekly_days.length) return showToast('error', '请至少选择一个星期')
  saving.value = true
  try {
    const method = editingTask.value ? 'PATCH' : 'POST'
    const path = editingTask.value ? `/tasks/${editingTask.value.id}` : '/tasks'
    await request(path, { method, body: JSON.stringify(taskPayload()) })
    taskModalOpen.value = false
    showToast('success', editingTask.value ? '任务计划已更新' : '常规任务已创建')
    await loadAll({ quiet: true })
  } catch (error) { showToast('error', '保存失败', error.message) }
  finally { saving.value = false }
}

async function runTask(task) {
  try {
    const result = await request(`/tasks/${task.id}/run`, { method: 'POST' })
    showToast('success', '任务已开始', '日志会在执行过程中持续更新')
    view.value = 'executions'
    await loadAll({ quiet: true })
    detail.value = executions.value.find((item) => item.id === result.execution_id) || null
  } catch (error) { showToast('error', '无法运行任务', error.message) }
}

async function toggleTask(task) {
  try {
    await request(`/tasks/${task.id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !Boolean(task.enabled) }) })
    showToast('success', task.enabled ? '任务计划已停用' : '任务计划已启用')
    await loadAll({ quiet: true })
  } catch (error) { showToast('error', '状态更新失败', error.message) }
}

async function confirmDelete() {
  if (!deletingTask.value) return
  try {
    await request(`/tasks/${deletingTask.value.id}`, { method: 'DELETE' })
    showToast('success', '任务计划已删除')
    deletingTask.value = null
    await loadAll({ quiet: true })
  } catch (error) { showToast('error', '删除失败', error.message) }
}

function toggleWeekday(day) {
  taskForm.weekly_days = taskForm.weekly_days.includes(day)
    ? taskForm.weekly_days.filter((item) => item !== day)
    : [...taskForm.weekly_days, day].sort()
}

function resetFilters() { Object.assign(filters, { name: '', enabled: 'all', trigger_type: 'all', app_name: 'all' }) }
function triggerLabel(type) { return triggerOptions.find((item) => item.value === type)?.label || type }
function triggerDetail(task) {
  const config = task.trigger_config || {}
  if (task.trigger_type === 'once') return formatTime(task.next_run_at)
  if (task.trigger_type === 'interval') return `每 ${config.value} ${intervalUnitLabel(config.unit)}`
  if (task.trigger_type === 'daily') return `每天 ${config.time}`
  if (task.trigger_type === 'weekly') return `周${(config.weekdays || []).map((day) => weekdayOptions.find((item) => item.value === day)?.label).join('、')} ${config.time}`
  return '按需手动运行'
}
function intervalUnitLabel(unit) { return ({ seconds: '秒', minutes: '分钟', hours: '小时', days: '天' })[unit] || unit }
function sourceLabel(source) { return source === 'schedule' ? '定时调度' : '手动运行' }
function statusLabel(status) {
  return ({ idle: '尚未运行', pending: '等待启动', running: '运行中', success: '成功', failed: '失败', timeout: '超时', cancelled: '已取消' })[status] || status || '未知'
}
function notificationLabel(status) {
  return ({ pending: '等待发送', sent: '已发送', skipped: '未配置', disabled: '已关闭', failed: '发送失败' })[status] || status || '—'
}
function formatDuration(value) {
  if (value == null) return '—'
  if (value < 1000) return `${value}ms`
  const seconds = Math.floor(value / 1000)
  if (seconds < 60) return `${seconds}秒`
  const minutes = Math.floor(seconds / 60)
  return minutes < 60 ? `${minutes}分${seconds % 60}秒` : `${Math.floor(minutes / 60)}小时${minutes % 60}分`
}
function formatTime(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function shortPath(path) {
  if (!path) return '—'
  const parts = path.replaceAll('\\', '/').split('/')
  return parts.length > 4 ? `…/${parts.slice(-3).join('/')}` : path
}
function openExecution(item) { detail.value = item }
function handleKeydown(event) {
  if (event.key !== 'Escape') return
  taskModalOpen.value = false; detail.value = null; deletingTask.value = null; toast.visible = false
}

watch(activeExecutions, (items) => {
  window.clearInterval(pollTimer)
  pollTimer = window.setInterval(() => loadAll({ quiet: true }), items.length ? 1200 : 5000)
}, { immediate: true })
onMounted(() => { document.addEventListener('keydown', handleKeydown); loadAll() })
onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeydown)
  window.clearInterval(pollTimer); window.clearTimeout(toastTimer)
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand-block">
        <div class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></div>
        <div><strong>SpiderFly</strong><small>本地任务管理</small></div>
      </div>

      <nav class="sidebar-nav" aria-label="主导航">
        <button
          v-for="item in navItems"
          :key="item.id"
          type="button"
          :class="{ active: view === item.id }"
          :aria-current="view === item.id ? 'page' : undefined"
          @click="view = item.id"
        >
          <span class="nav-mark">{{ item.mark }}</span>
          <span>{{ item.label }}</span>
          <i v-if="item.id === 'executions' && activeExecutions.length">{{ activeExecutions.length }}</i>
        </button>
      </nav>

      <div class="local-mode-card">
        <span class="status-dot success"></span>
        <div><strong>本地直跑模式</strong><small>无需 Agent 与配对码</small></div>
      </div>
      <div class="sidebar-footer">SpiderFly · v0.2</div>
    </aside>

    <main class="workspace">
      <header class="workspace-header">
        <div>
          <span class="eyebrow">SPIDERFLY / LOCAL AUTOMATION</span>
          <h1>{{ pageTitle }}</h1>
        </div>
        <button v-if="view === 'overview'" class="button primary" type="button" @click="openCreate">
          <span aria-hidden="true">＋</span> 新建常规任务
        </button>
      </header>

      <div v-if="loading" class="loading-panel" role="status">
        <span class="spinner"></span>正在载入本地任务…
      </div>

      <template v-else>
        <section v-if="view === 'overview'" class="view-stack">
          <div class="metric-grid">
            <article class="metric-card">
              <span>任务总数</span><strong>{{ overview.total_tasks || 0 }}</strong><small>{{ overview.enabled_tasks || 0 }} 个已启用</small>
            </article>
            <article class="metric-card">
              <span>正在运行</span><strong>{{ overview.running_tasks || 0 }}</strong><small>实时刷新执行日志</small>
            </article>
            <article class="metric-card">
              <span>今日成功</span><strong class="success-text">{{ overview.success_runs || 0 }}</strong><small>共运行 {{ overview.total_runs || 0 }} 次</small>
            </article>
            <article class="metric-card">
              <span>今日异常</span><strong :class="{ 'danger-text': overview.failed_runs }">{{ overview.failed_runs || 0 }}</strong><small>失败与超时</small>
            </article>
          </div>

          <div v-if="attentionTasks.length" class="notice warning">
            <span class="notice-icon">!</span>
            <div><strong>{{ attentionTasks.length }} 个任务最近一次运行异常</strong><small>进入执行记录查看错误日志和飞书通知结果。</small></div>
            <button class="button ghost compact" type="button" @click="view = 'executions'">查看记录</button>
          </div>

          <div class="two-column">
            <section class="panel">
              <header class="panel-heading">
                <div><h2>最近执行</h2><p>每个任务结束后只生成一条最终通知。</p></div>
                <button class="text-button" type="button" @click="view = 'executions'">全部记录</button>
              </header>
              <div v-if="recentExecutions.length" class="run-list">
                <button v-for="item in recentExecutions" :key="item.id" type="button" @click="openExecution(item)">
                  <span class="status-dot" :class="item.status"></span>
                  <span class="run-copy"><strong>{{ item.task_name }}</strong><small>{{ formatTime(item.created_at) }}</small></span>
                  <span class="run-result"><strong>{{ statusLabel(item.status) }}</strong><small>{{ formatDuration(item.duration_ms) }}</small></span>
                </button>
              </div>
              <div v-else class="empty-state compact-empty"><strong>还没有执行记录</strong><p>运行示例任务后，日志会显示在这里。</p></div>
            </section>

            <section class="panel">
              <header class="panel-heading"><div><h2>通知策略</h2><p>保持安静，只报告最终结果。</p></div></header>
              <div class="policy-list">
                <div><span class="policy-index success-bg">01</span><p><strong>成功：一句话</strong><small>任务名、成功状态与实际耗时。</small></p></div>
                <div><span class="policy-index danger-bg">02</span><p><strong>失败：一条图文</strong><small>错误摘要和当前窗口截图，仍是一条消息。</small></p></div>
                <div><span class="policy-index neutral-bg">03</span><p><strong>不发送开始提醒</strong><small>不同运行时长不会造成消息干扰。</small></p></div>
              </div>
              <div class="config-state" :class="settings.feishu_configured ? 'configured' : 'unconfigured'">
                <span class="status-dot" :class="settings.feishu_configured ? 'success' : 'idle'"></span>
                <div><strong>{{ settings.feishu_configured ? '飞书通知已配置' : '飞书通知等待配置' }}</strong><small>任务执行和日志记录不受通知配置影响。</small></div>
                <button class="text-button" type="button" @click="view = 'settings'">查看设置</button>
              </div>
            </section>
          </div>
        </section>

        <section v-else-if="view === 'tasks'" class="view-stack">
          <section class="panel scheduler-toolbar">
            <header class="panel-heading"><div><h2>常规任务计划</h2><p>统一管理个人 Python 程序的触发时间、启停状态与执行结果。</p></div></header>
            <div class="filter-bar">
              <label class="filter-field"><span>任务名称</span><input v-model="filters.name" type="search" placeholder="输入任务名称" /></label>
              <label class="filter-field"><span>启用状态</span><select v-model="filters.enabled"><option value="all">全部状态</option><option value="enabled">已启用</option><option value="disabled">已停用</option></select></label>
              <label class="filter-field"><span>触发方式</span><select v-model="filters.trigger_type"><option value="all">全部触发方式</option><option v-for="option in triggerOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
              <label class="filter-field"><span>应用名称</span><select v-model="filters.app_name"><option value="all">全部 Python 应用</option><option v-for="name in appOptions" :key="name" :value="name">{{ name }}</option></select></label>
              <div class="filter-actions">
                <button class="button primary" type="button" @click="openCreate"><span aria-hidden="true">＋</span> 新建常规任务</button>
                <button class="icon-button toolbar-icon" type="button" title="刷新任务" aria-label="刷新任务" @click="loadAll({ quiet: true })">↻</button>
                <button class="icon-button toolbar-icon" type="button" title="调度设置" aria-label="调度设置" @click="view = 'settings'">⚙</button>
                <button class="icon-button toolbar-icon" type="button" title="清空筛选" aria-label="清空筛选" @click="resetFilters">···</button>
              </div>
            </div>
          </section>
          <section class="panel table-panel">
            <header class="panel-heading"><div><h2>计划列表</h2><p>显示 {{ filteredTasks.length }} / {{ tasks.length }} 个计划 · 同一计划不会重复并发运行</p></div></header>
            <div v-if="filteredTasks.length" class="table-wrap">
              <table>
                <thead><tr><th>任务名称</th><th>启用状态</th><th>触发方式</th><th>应用名称</th><th>下次运行</th><th>最近状态</th><th class="align-right">操作</th></tr></thead>
                <tbody>
                  <tr v-for="task in filteredTasks" :key="task.id">
                    <td><div class="primary-cell"><strong>{{ task.name }}</strong><small>{{ task.description || '暂无说明' }}</small></div></td>
                    <td><button class="plan-state" :class="{ enabled: task.enabled }" type="button" @click="toggleTask(task)"><i></i>{{ task.enabled ? '已启用' : '已停用' }}</button></td>
                    <td><div class="primary-cell"><strong>{{ triggerLabel(task.trigger_type) }}</strong><small>{{ triggerDetail(task) }}</small></div></td>
                    <td><div class="primary-cell"><strong>{{ task.app_name }}</strong><small :title="task.script_path">{{ shortPath(task.script_path) }}</small></div></td>
                    <td><span :class="{ muted: !task.next_run_at }">{{ task.enabled ? formatTime(task.next_run_at) : '停用后不调度' }}</span></td>
                    <td><span class="status-badge"><i class="status-dot" :class="task.last_status"></i>{{ statusLabel(task.last_status) }}</span></td>
                    <td class="align-right"><div class="row-actions">
                      <button class="button primary compact" type="button" :disabled="!task.enabled || task.last_status === 'running'" @click="runTask(task)">{{ task.last_status === 'running' ? '运行中' : '运行' }}</button>
                      <button class="icon-button" type="button" aria-label="编辑任务" title="编辑任务" @click="openEdit(task)">✎</button>
                      <button class="icon-button danger-icon" type="button" aria-label="删除任务" title="删除任务" @click="deletingTask = task">×</button>
                    </div></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else-if="tasks.length" class="empty-state"><strong>没有匹配的任务计划</strong><p>调整上方筛选条件，或清空全部筛选。</p><button class="button secondary" type="button" @click="resetFilters">清空筛选</button></div>
            <div v-else class="empty-state"><div class="empty-canopy"><span></span><span></span><span></span></div><strong>还没有常规任务计划</strong><p>选择本机 `.py` 文件并设置触发时间，即可自动运行和监控日志。</p><button class="button secondary" type="button" @click="openCreate">创建第一个计划</button></div>
          </section>
        </section>

        <section v-else-if="view === 'executions'" class="view-stack">
          <div class="section-summary"><p>stdout 与 stderr 在程序运行期间持续写入。选择记录可查看完整日志与飞书通知状态。</p></div>
          <section class="panel table-panel">
            <header class="panel-heading"><div><h2>执行记录</h2><p>最近 {{ executions.length }} 条</p></div><button class="button secondary compact" type="button" @click="loadAll({ quiet: true })">刷新</button></header>
            <div v-if="executions.length" class="table-wrap">
              <table>
                <thead><tr><th>状态</th><th>任务</th><th>触发来源</th><th>开始时间</th><th>耗时</th><th>退出码</th><th>飞书通知</th><th></th></tr></thead>
                <tbody>
                  <tr v-for="item in executions" :key="item.id" class="clickable-row" tabindex="0" @click="openExecution(item)" @keydown.enter="openExecution(item)">
                    <td><span class="status-badge"><i class="status-dot" :class="item.status"></i>{{ statusLabel(item.status) }}</span></td>
                    <td><div class="primary-cell"><strong>{{ item.task_name }}</strong><small>#{{ item.id }}</small></div></td>
                    <td>{{ sourceLabel(item.trigger_source) }}</td>
                    <td>{{ formatTime(item.started_at || item.created_at) }}</td>
                    <td>{{ formatDuration(item.duration_ms) }}</td>
                    <td><code>{{ item.exit_code == null ? '—' : item.exit_code }}</code></td>
                    <td>{{ notificationLabel(item.notification_status) }}</td>
                    <td><button class="text-button" type="button" @click.stop="openExecution(item)">查看日志</button></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="empty-state"><strong>暂无执行记录</strong><p>前往任务管理运行一个 Python 脚本。</p><button class="button secondary" type="button" @click="view = 'tasks'">打开任务管理</button></div>
          </section>
        </section>

        <section v-else class="view-stack settings-grid">
          <section class="panel">
            <header class="panel-heading"><div><h2>运行与调度</h2><p>SpiderFly 采用本地直跑，并由统一调度器触发计划。</p></div><span class="mini-badge success-badge">{{ settings.scheduler === 'running' ? '运行中' : '已停止' }}</span></header>
            <div class="setting-rows">
              <div><span>执行模式</span><strong>local-direct</strong><small>后端直接启动本机 Python，无 Agent、设备和配对码。</small></div>
              <div><span>调度时区</span><strong>{{ settings.scheduler_timezone || 'Asia/Shanghai' }}</strong><small>所有单次、每日和每周计划都按北京时间计算。</small></div>
              <div><span>重叠策略</span><strong>{{ settings.collision_policy || 'skip-overlapping-run' }}</strong><small>上一次尚未结束时跳过本轮，避免同一计划重复并发。</small></div>
              <div><span>通知策略</span><strong>one-final-message</strong><small>运行结束后最多发送一条飞书消息。</small></div>
            </div>
          </section>
          <section class="panel">
            <header class="panel-heading"><div><h2>飞书配置</h2><p>凭据只从后端环境变量读取，不在网页中显示或保存。</p></div><span class="mini-badge" :class="settings.feishu_configured ? 'success-badge' : 'warning-badge'">{{ settings.feishu_configured ? '已配置' : '待配置' }}</span></header>
            <div class="env-list">
              <code>FEISHU_APP_ID</code><span>飞书自建应用 ID</span>
              <code>FEISHU_APP_SECRET</code><span>飞书自建应用密钥</span>
              <code>FEISHU_RECEIVER_ID</code><span>接收人 open_id 或手机号</span>
              <code>FEISHU_RECEIVER_ID_TYPE</code><span>open_id（推荐）或 mobile</span>
            </div>
            <div class="notice info"><span class="notice-icon">i</span><div><strong>未配置时仍可正常运行任务</strong><small>执行日志会完整保存，通知状态显示为“未配置”。</small></div></div>
          </section>
        </section>
      </template>
    </main>

    <div v-if="taskModalOpen" class="modal-layer" @mousedown.self="taskModalOpen = false">
      <section class="modal plan-modal" role="dialog" aria-modal="true" :aria-label="editingTask ? '编辑任务计划' : '新建常规任务'">
        <header><div><span class="eyebrow">ROUTINE TASK PLAN</span><h2>{{ editingTask ? '编辑任务计划' : '新建常规任务' }}</h2></div><button class="modal-close" type="button" aria-label="关闭" @click="taskModalOpen = false">×</button></header>
        <div class="modal-body">
          <div class="field-grid">
            <label class="field"><span>任务名称</span><input v-model="taskForm.name" type="text" maxlength="100" placeholder="例如：订单同步" /></label>
            <label class="field"><span>应用名称</span><input v-model="taskForm.app_name" type="text" maxlength="100" placeholder="留空时使用脚本文件名" /></label>
          </div>
          <label class="field"><span>任务说明</span><input v-model="taskForm.description" type="text" maxlength="500" placeholder="这个计划负责什么" /></label>
          <label class="field"><span>Python 脚本路径</span><input v-model="taskForm.script_path" class="code-input" type="text" placeholder="D:\automation\main.py" /><small>必须是这台电脑上真实存在的 .py 文件。</small></label>
          <label class="field"><span>Python 解释器（可选）</span><input v-model="taskForm.python_path" class="code-input" type="text" placeholder="留空使用 SpiderFly 当前 Python" /></label>

          <section class="schedule-card">
            <div class="schedule-card-heading"><div><strong>触发方式</strong><small>到达计划时间后自动启动该 Python 应用。</small></div><span>北京时间 UTC+8</span></div>
            <div class="trigger-choice-grid">
              <button v-for="option in triggerOptions" :key="option.value" type="button" :class="{ active: taskForm.trigger_type === option.value }" @click="taskForm.trigger_type = option.value"><i></i>{{ option.label }}</button>
            </div>
            <div v-if="taskForm.trigger_type === 'manual'" class="schedule-hint">仅通过“运行”按钮启动，不自动执行。</div>
            <label v-else-if="taskForm.trigger_type === 'once'" class="field"><span>执行日期与时间</span><input v-model="taskForm.once_at" type="datetime-local" /></label>
            <div v-else-if="taskForm.trigger_type === 'interval'" class="field-grid interval-fields">
              <label class="field"><span>间隔数值</span><input v-model.number="taskForm.interval_value" type="number" min="1" max="100000" /></label>
              <label class="field"><span>时间单位</span><select v-model="taskForm.interval_unit"><option value="minutes">分钟</option><option value="hours">小时</option><option value="days">天</option></select></label>
            </div>
            <label v-else-if="taskForm.trigger_type === 'daily'" class="field"><span>每天执行时间</span><input v-model="taskForm.daily_time" type="time" /></label>
            <div v-else class="weekly-fields">
              <div class="field"><span>执行星期</span><div class="weekday-grid"><button v-for="day in weekdayOptions" :key="day.value" type="button" :class="{ active: taskForm.weekly_days.includes(day.value) }" @click="toggleWeekday(day.value)">周{{ day.label }}</button></div></div>
              <label class="field"><span>执行时间</span><input v-model="taskForm.weekly_time" type="time" /></label>
            </div>
          </section>

          <div class="field-grid">
            <label class="field"><span>超时时间（秒）</span><input v-model.number="taskForm.timeout_seconds" type="number" min="0" max="604800" /><small>0 表示不限时。</small></label>
            <div class="field"><span>计划状态</span><button class="switch-row" type="button" role="switch" :aria-checked="taskForm.enabled" @click="taskForm.enabled = !taskForm.enabled"><i :class="{ active: taskForm.enabled }"><b></b></i><span>{{ taskForm.enabled ? '创建后立即启用' : '暂不启用' }}</span></button></div>
          </div>
          <div class="notification-options">
            <div><strong>最终通知</strong><small>不发送开始消息；每次运行最多一条。</small></div>
            <label><input v-model="taskForm.notify_on_success" type="checkbox" />成功时发送一句话</label>
            <label><input v-model="taskForm.notify_on_failure" type="checkbox" />失败时发送图文消息</label>
          </div>
        </div>
        <footer><button class="button ghost" type="button" @click="taskModalOpen = false">取消</button><button class="button primary" type="button" :disabled="saving" @click="saveTask">{{ saving ? '正在保存…' : editingTask ? '保存修改' : '创建任务计划' }}</button></footer>
      </section>
    </div>

    <div v-if="detail" class="modal-layer" @mousedown.self="detail = null">
      <section class="modal log-modal" role="dialog" aria-modal="true" aria-label="执行日志">
        <header><div><span class="eyebrow">EXECUTION #{{ detail.id }}</span><h2>{{ detail.task_name }}</h2></div><button class="modal-close" type="button" aria-label="关闭" @click="detail = null">×</button></header>
        <div class="execution-summary">
          <span class="status-badge"><i class="status-dot" :class="detail.status"></i>{{ statusLabel(detail.status) }}</span>
          <span>耗时 <strong>{{ formatDuration(detail.duration_ms) }}</strong></span>
          <span>退出码 <code>{{ detail.exit_code == null ? '—' : detail.exit_code }}</code></span>
          <span>飞书 <strong>{{ notificationLabel(detail.notification_status) }}</strong></span>
        </div>
        <div class="modal-body log-body">
          <div><span class="log-label">标准输出 stdout</span><pre>{{ detail.stdout || (['pending', 'running'].includes(detail.status) ? '等待程序输出…' : '（无输出）') }}</pre></div>
          <div v-if="detail.stderr || detail.error_message"><span class="log-label danger-text">错误输出 stderr</span><pre class="error-log">{{ detail.stderr || detail.error_message }}</pre></div>
          <div v-if="detail.notification_error" class="notice warning"><span class="notice-icon">!</span><div><strong>飞书通知未发送</strong><small>{{ detail.notification_error }}</small></div></div>
        </div>
        <footer><button class="button secondary" type="button" @click="detail = null">关闭日志</button></footer>
      </section>
    </div>

    <div v-if="deletingTask" class="modal-layer" @mousedown.self="deletingTask = null">
      <section class="modal confirm-modal" role="alertdialog" aria-modal="true" aria-label="删除任务">
        <header><div><span class="eyebrow danger-text">DANGER</span><h2>删除“{{ deletingTask.name }}”</h2></div><button class="modal-close" type="button" aria-label="关闭" @click="deletingTask = null">×</button></header>
        <div class="modal-body"><p>任务和历史执行记录会一起删除，原始 Python 文件不会受到影响。</p></div>
        <footer><button class="button ghost" type="button" @click="deletingTask = null">取消</button><button class="button danger" type="button" @click="confirmDelete">删除任务</button></footer>
      </section>
    </div>

    <div v-if="toast.visible" class="toast" :class="toast.type" role="status">
      <span class="toast-icon">{{ toast.type === 'success' ? '✓' : '!' }}</span><span><strong>{{ toast.title }}</strong><small v-if="toast.message">{{ toast.message }}</small></span><button type="button" aria-label="关闭通知" @click="toast.visible = false">×</button>
    </div>
  </div>
</template>
