<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

const API = '/api'
const baseNavItems = [
  { id: 'overview', label: '工作台', mark: 'W' },
  { id: 'tasks', label: '任务中心', mark: 'T' },
  { id: 'runtime', label: '运行中心', mark: 'R' },
]
const adminNavItems = [
  { id: 'management', label: '管理中心', mark: 'M' },
]
const runtimeTabs = [
  { id: 'queue', label: '任务时间表' },
  { id: 'executions', label: '运行记录' },
]
const managementTabs = [
  { id: 'apps', label: '创建任务' },
  { id: 'users', label: '成员管理' },
  { id: 'settings', label: '系统设置' },
  { id: 'audit', label: '操作审计' },
]
const triggerOptions = [
  { value: 'manual', label: '手动触发' },
  { value: 'daily', label: '每日执行' },
  { value: 'weekly', label: '每周执行' },
]
const weekdayOptions = [
  { value: 1, label: '一' }, { value: 2, label: '二' }, { value: 3, label: '三' },
  { value: 4, label: '四' }, { value: 5, label: '五' }, { value: 6, label: '六' },
  { value: 7, label: '日' },
]

const authChecking = ref(true)
const me = ref(null)
const view = ref('overview')
const runtimeTab = ref('queue')
const scheduleScope = ref('today')
const managementTab = ref('apps')
const loading = ref(false)
const tasks = ref([])
const executions = ref([])
const users = ref([])
const auditLogs = ref([])
const overview = ref({})
const settings = ref({})
const taskModalOpen = ref(false)
const editingTask = ref(null)
const detail = ref(null)
const artifactDownload = reactive({ busy: false, path: '', message: '', error: false })
const deletingTask = ref(null)
const changePasswordOpen = ref(false)
const saving = ref(false)
const uploading = ref(false)
const creatingUser = ref(false)
const loginBusy = ref(false)
const uploadKey = ref(0)
const toast = reactive({ visible: false, type: 'success', title: '', message: '' })
const filters = reactive({ name: '', enabled: 'all', trigger_type: 'all' })
const loginForm = reactive({ username: '', password: '' })
const passwordForm = reactive({ current_password: '', new_password: '', confirm_password: '' })
const appForm = reactive({
  name: '', description: '', requirements_text: '', requirements_filename: '', script: null, template: null,
  enabled: true, notify_on_success: true, notify_on_failure: true, trigger_type: 'manual',
  daily_time: '09:00', weekly_days: [1], weekly_time: '09:00',
})
const userForm = reactive({ username: '', display_name: '', role: 'operator', password: '' })
const taskForm = reactive({
  name: '', description: '', app_id: '', timeout_seconds: 600, enabled: true,
  notify_on_success: true, notify_on_failure: true, trigger_type: 'manual',
  daily_time: '09:00', weekly_days: [1], weekly_time: '09:00',
})

let toastTimer = 0
let pollTimer = 0
let artifactDownloadController = null
let artifactDownloadGeneration = 0

const isAdmin = computed(() => me.value?.role === 'admin')
const navItems = computed(() => isAdmin.value ? [...baseNavItems, ...adminNavItems] : baseNavItems)
const pageTitle = computed(() => navItems.value.find((item) => item.id === view.value)?.label || 'SpiderFly')
const activeExecutions = computed(() => executions.value.filter((item) => ['pending', 'running'].includes(item.status)))
const runningExecution = computed(() => executions.value.find((item) => item.status === 'running') || null)
const queuedExecutions = computed(() => executions.value
  .filter((item) => item.status === 'pending')
  .sort((a, b) => (a.queue_position ?? 999999) - (b.queue_position ?? 999999)))
const completedExecutions = computed(() => executions.value.filter((item) => !['pending', 'running'].includes(item.status)))
const detailFinished = computed(() => ['success', 'failed', 'timeout', 'cancelled'].includes(detail.value?.status))
const artifactFiles = computed(() => Array.isArray(detail.value?.artifacts?.files) ? detail.value.artifacts.files : [])
const manualTasks = computed(() => tasks.value.filter((task) => task.enabled && task.trigger_type === 'manual'))
const taskScheduleDays = computed(() => {
  const todayKey = shanghaiDateKey(Date.now())
  if (scheduleScope.value === 'today') return [buildScheduleDay(todayKey)]
  const weekStartKey = addDaysToDateKey(todayKey, 1 - isoWeekday(todayKey))
  return Array.from({ length: 7 }, (_, index) => buildScheduleDay(addDaysToDateKey(weekStartKey, index)))
})
const recentExecutions = computed(() => completedExecutions.value.slice(0, 6))
const attentionTasks = computed(() => tasks.value.filter((item) => ['failed', 'timeout'].includes(item.last_status)))
const readyTasks = computed(() => tasks.value.filter((item) => item.environment_status === 'ready'))
const buildingTasks = computed(() => tasks.value.filter((item) => ['pending', 'building', 'not_built'].includes(item.environment_status)))
const filteredTasks = computed(() => tasks.value.filter((task) => {
  const taskName = String(task.name || '').toLowerCase()
  const nameMatch = !filters.name.trim() || taskName.includes(filters.name.trim().toLowerCase())
  const enabledMatch = filters.enabled === 'all' || Boolean(task.enabled) === (filters.enabled === 'enabled')
  const triggerMatch = filters.trigger_type === 'all' || task.trigger_type === filters.trigger_type
  return nameMatch && enabledMatch && triggerMatch
}))

watch(() => detail.value?.id, resetArtifactDownload, { flush: 'sync' })

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) }
  if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }
  const response = await fetch(API + path, { credentials: 'include', ...options, headers })
  if (!response.ok) {
    let message = '请求失败（' + response.status + '）'
    try {
      const data = await response.json()
      if (typeof data.detail === 'string') message = data.detail
      else if (Array.isArray(data.detail)) message = data.detail.map((item) => item.msg).filter(Boolean).join('；') || message
    } catch {}
    const error = new Error(message)
    error.status = response.status
    throw error
  }
  if (response.status === 204) return null
  return response.json()
}

function showToast(type, title, message = '') {
  Object.assign(toast, { visible: true, type, title, message })
  window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => { toast.visible = false }, 3600)
}

function clearSharedData() {
  tasks.value = []
  executions.value = []
  users.value = []
  auditLogs.value = []
  overview.value = {}
  settings.value = {}
  detail.value = null
}

function handleSessionExpired() {
  me.value = null
  clearSharedData()
  window.clearTimeout(pollTimer)
  showToast('error', '登录已过期', '请重新登录后继续使用')
}

async function loadAll({ quiet = false, includeAdmin = true } = {}) {
  if (!me.value || me.value.must_change_password) {
    loading.value = false
    return
  }
  if (!quiet) loading.value = true
  try {
    const baseResults = await Promise.all([
      request('/overview'),
      request('/settings'),
      request('/tasks'),
      request('/executions?limit=100'),
    ])
    overview.value = baseResults[0]
    settings.value = baseResults[1]
    tasks.value = baseResults[2]
    executions.value = baseResults[3]
    if (detail.value) {
      const detailId = detail.value.id
      const refreshed = await request('/executions/' + detailId)
      if (detail.value?.id === detailId) detail.value = refreshed
    }
    if (includeAdmin && isAdmin.value) {
      const adminResults = await Promise.all([
        request('/users'),
        request('/audit-logs?limit=100'),
      ])
      users.value = adminResults[0]
      auditLogs.value = adminResults[1]
    }
  } catch (error) {
    if (error.status === 401) handleSessionExpired()
    else if (!quiet) showToast('error', '数据暂时没有载入', error.message)
  } finally {
    loading.value = false
    schedulePoll()
  }
}

function schedulePoll() {
  window.clearTimeout(pollTimer)
  if (!me.value || me.value.must_change_password) return
  const delay = activeExecutions.value.length || buildingTasks.value.length ? 1200 : 5000
  pollTimer = window.setTimeout(async () => {
    await loadAll({ quiet: true, includeAdmin: false })
  }, delay)
}

async function checkAuth() {
  authChecking.value = true
  try {
    me.value = await request('/auth/me')
    changePasswordOpen.value = Boolean(me.value.must_change_password)
    if (!me.value.must_change_password) await loadAll()
  } catch (error) {
    if (error.status !== 401) showToast('error', '无法连接 SpiderFly', error.message)
    me.value = null
  } finally {
    authChecking.value = false
  }
}

async function login() {
  if (!loginForm.username.trim() || !loginForm.password) {
    showToast('error', '请输入账号和密码')
    return
  }
  loginBusy.value = true
  try {
    await request('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: loginForm.username.trim(), password: loginForm.password }),
    })
    me.value = await request('/auth/me')
    loginForm.password = ''
    view.value = 'overview'
    changePasswordOpen.value = Boolean(me.value.must_change_password)
    if (!me.value.must_change_password) await loadAll()
    showToast('success', '欢迎回来', me.value.display_name || me.value.username)
  } catch (error) {
    showToast('error', '登录失败', error.message)
  } finally {
    loginBusy.value = false
  }
}

async function logout() {
  try {
    await request('/auth/logout', { method: 'POST' })
  } catch {}
  me.value = null
  changePasswordOpen.value = false
  passwordForm.current_password = ''
  passwordForm.new_password = ''
  passwordForm.confirm_password = ''
  clearSharedData()
  window.clearTimeout(pollTimer)
}

async function changePassword() {
  if (!passwordForm.current_password || !passwordForm.new_password) {
    showToast('error', '请填写当前密码和新密码')
    return
  }
  if (passwordForm.new_password !== passwordForm.confirm_password) {
    showToast('error', '两次输入的新密码不一致')
    return
  }
  saving.value = true
  try {
    await request('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: passwordForm.current_password,
        new_password: passwordForm.new_password,
      }),
    })
    me.value = { ...me.value, must_change_password: false }
    changePasswordOpen.value = false
    passwordForm.current_password = ''
    passwordForm.new_password = ''
    passwordForm.confirm_password = ''
    showToast('success', '密码已更新')
    await loadAll()
  } catch (error) {
    showToast('error', '密码修改失败', error.message)
  } finally {
    saving.value = false
  }
}

function navigateTo(target, tab = null) {
  if (target === 'management' && !isAdmin.value) {
    view.value = 'overview'
    showToast('error', '此区域仅管理员可用', '普通成员可使用工作台、任务中心和运行中心')
    return
  }
  if (target === 'runtime' && runtimeTabs.some((item) => item.id === tab)) runtimeTab.value = tab
  if (target === 'management' && managementTabs.some((item) => item.id === tab)) managementTab.value = tab
  view.value = target
}

function openCreate() {
  if (isAdmin.value) {
    navigateTo('management', 'apps')
    return
  }
  showToast('error', '请联系管理员创建任务')
}

function openEdit(task) {
  if (taskIsActive(task)) {
    showToast('error', '任务正在排队或运行', '当前只能停用计划，完成后才能修改其他内容')
    return
  }
  editingTask.value = task
  const config = task.trigger_config || {}
  Object.assign(taskForm, {
    name: task.name || '',
    description: task.description || '',
    app_id: task.app_id || '',
    timeout_seconds: 600,
    enabled: Boolean(task.enabled),
    notify_on_success: Boolean(task.notify_on_success),
    notify_on_failure: Boolean(task.notify_on_failure),
    trigger_type: triggerOptions.some((item) => item.value === task.trigger_type) ? task.trigger_type : 'manual',
    daily_time: config.time || '09:00',
    weekly_days: config.weekdays || [1],
    weekly_time: config.time || '09:00',
  })
  taskModalOpen.value = true
}

function triggerConfig(form) {
  if (form.trigger_type === 'daily') return { time: form.daily_time }
  if (form.trigger_type === 'weekly') return { weekdays: form.weekly_days, time: form.weekly_time }
  return {}
}

function taskPayload() {
  const payload = {
    name: taskForm.name.trim(),
    description: taskForm.description.trim(),
    timeout_seconds: 600,
    enabled: taskForm.enabled,
    notify_on_success: taskForm.notify_on_success,
    notify_on_failure: taskForm.notify_on_failure,
    trigger_type: taskForm.trigger_type,
    trigger_config: triggerConfig(taskForm),
  }
  if (editingTask.value) payload.version = editingTask.value.version
  return payload
}

async function saveTask() {
  if (!taskForm.name.trim()) {
    showToast('error', '请填写任务名称')
    return
  }
  if (taskForm.trigger_type === 'weekly' && !taskForm.weekly_days.length) {
    showToast('error', '请至少选择一个星期')
    return
  }
  saving.value = true
  try {
    const method = editingTask.value ? 'PATCH' : 'POST'
    const path = editingTask.value ? '/tasks/' + editingTask.value.id : '/tasks'
    await request(path, { method, body: JSON.stringify(taskPayload()) })
    taskModalOpen.value = false
    showToast('success', '任务已更新')
    await loadAll({ quiet: true, includeAdmin: false })
  } catch (error) {
    if (error.status === 409 && error.message.includes('其他伙伴修改')) {
      showToast('error', '这项任务已被其他人修改', '已刷新最新内容，请重新打开后再编辑')
      taskModalOpen.value = false
      await loadAll({ quiet: true, includeAdmin: false })
    } else {
      showToast('error', '保存失败', error.message)
    }
  } finally {
    saving.value = false
  }
}

async function runTask(task) {
  try {
    const result = await request('/tasks/' + task.id + '/run', { method: 'POST' })
    const position = result?.queue_position
    showToast('success', position ? '任务已加入队列' : '运行请求已提交', position ? '当前排在第 ' + position + ' 位' : '共享电脑会按顺序执行')
    navigateTo('runtime', 'queue')
    await loadAll({ quiet: true, includeAdmin: false })
    detail.value = executions.value.find((item) => item.id === result?.execution_id) || null
  } catch (error) {
    showToast('error', '无法运行任务', error.message)
  }
}

async function toggleTask(task) {
  try {
    await request('/tasks/' + task.id, {
      method: 'PATCH',
      body: JSON.stringify({ enabled: !Boolean(task.enabled), version: task.version }),
    })
    showToast('success', task.enabled ? '任务已停用' : '任务已启用')
    await loadAll({ quiet: true, includeAdmin: false })
  } catch (error) {
    if (error.status === 409 && error.message.includes('其他伙伴修改')) {
      showToast('error', '状态已被其他人更新', '页面已自动刷新')
    }
    else showToast('error', '状态更新失败', error.message)
    await loadAll({ quiet: true, includeAdmin: false })
  }
}

async function confirmDelete() {
  if (!deletingTask.value || !isAdmin.value) return
  try {
    await request('/tasks/' + deletingTask.value.id, { method: 'DELETE' })
    showToast('success', '任务已彻底删除', '程序、独立环境和运行记录已清理')
    deletingTask.value = null
    await loadAll({ quiet: true, includeAdmin: false })
  } catch (error) {
    showToast('error', '删除失败', error.message)
  }
}

async function cancelExecution(item) {
  try {
    await request('/executions/' + item.id + '/cancel', { method: 'POST' })
    showToast('success', item.status === 'pending' ? '已从队列移除' : '已提交停止请求')
    await loadAll({ quiet: true, includeAdmin: false })
  } catch (error) {
    showToast('error', '无法取消', error.message)
  }
}

function selectScript(event) {
  appForm.script = event.target.files?.[0] || null
  if (!appForm.name && appForm.script) appForm.name = appForm.script.name.replace(/\.py$/i, '')
}

function selectTemplate(event) {
  appForm.template = event.target.files?.[0] || null
}

async function selectRequirementsFile(event) {
  const input = event.currentTarget
  const file = input.files?.[0]
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.txt')) {
    input.value = ''
    showToast('error', '依赖文件格式不对', '请选择 requirements.txt 文本文件')
    return
  }
  if (file.size > 100 * 1024) {
    input.value = ''
    showToast('error', '依赖文件过大', 'requirements.txt 内容不能超过 20,000 个字符')
    return
  }
  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())
      .replace(/^\uFEFF/, '')
      .replace(/\r\n?/g, '\n')
      .trim()
    if (!text) throw new Error('requirements.txt 是空文件')
    if (text.length > 20000) throw new Error('requirements.txt 内容不能超过 20,000 个字符')
    if (text.includes('\0')) throw new Error('requirements.txt 不是有效的文本文件')
    appForm.requirements_text = text
    appForm.requirements_filename = file.name
    showToast('success', '依赖清单已读取', '确认后点击“创建任务”')
  } catch (error) {
    input.value = ''
    appForm.requirements_filename = ''
    showToast('error', '无法读取依赖文件', error.message || '请确认文件使用 UTF-8 编码')
  }
}

async function uploadApp() {
  if (!isAdmin.value) return
  if (!appForm.name.trim() || !appForm.script) {
    showToast('error', '请选择 Python 脚本并填写任务名称')
    return
  }
  if (!appForm.script.name.toLowerCase().endsWith('.py')) {
    showToast('error', '只能上传 .py 文件')
    return
  }
  if (appForm.template && !/\.(xlsx|xlsm|xltx|xltm)$/i.test(appForm.template.name)) {
    showToast('error', '模板格式不支持', '请选择 .xlsx、.xlsm、.xltx 或 .xltm 文件')
    return
  }
  if (appForm.trigger_type === 'weekly' && !appForm.weekly_days.length) {
    showToast('error', '请至少选择一个星期')
    return
  }
  if (appForm.trigger_type === 'daily' && !appForm.daily_time) {
    showToast('error', '请选择每天执行时间')
    return
  }
  if (appForm.trigger_type === 'weekly' && !appForm.weekly_time) {
    showToast('error', '请选择每周执行时间')
    return
  }
  uploading.value = true
  try {
    const form = new FormData()
    form.append('name', appForm.name.trim())
    form.append('description', appForm.description.trim())
    form.append('requirements_text', appForm.requirements_text.trim())
    form.append('trigger_type', appForm.trigger_type)
    form.append('trigger_config', JSON.stringify(triggerConfig(appForm)))
    form.append('enabled', String(appForm.enabled))
    form.append('notify_on_success', String(appForm.notify_on_success))
    form.append('notify_on_failure', String(appForm.notify_on_failure))
    form.append('script', appForm.script)
    if (appForm.template) form.append('template', appForm.template)
    await request('/apps', { method: 'POST', body: form })
    Object.assign(appForm, {
      name: '', description: '', requirements_text: '', requirements_filename: '', script: null, template: null,
      enabled: true, notify_on_success: true, notify_on_failure: true, trigger_type: 'manual',
      daily_time: '09:00', weekly_days: [1], weekly_time: '09:00',
    })
    uploadKey.value += 1
    await loadAll({ quiet: true, includeAdmin: false })
    navigateTo('tasks')
    showToast('success', '任务已一次创建完成', '运行设置已保存，正在准备独立环境')
  } catch (error) {
    showToast('error', '创建任务失败', error.message)
  } finally {
    uploading.value = false
  }
}

async function rebuildTaskEnvironment(task) {
  if (!isAdmin.value) return
  try {
    await request('/apps/' + task.app_id + '/rebuild', { method: 'POST' })
    showToast('success', '正在修复运行环境', task.name)
    await loadAll({ quiet: true, includeAdmin: false })
  } catch (error) {
    showToast('error', '无法修复运行环境', error.message)
  }
}

async function createUser() {
  if (!isAdmin.value) return
  if (!userForm.username.trim() || !userForm.display_name.trim() || !userForm.password) {
    showToast('error', '请完整填写成员信息')
    return
  }
  creatingUser.value = true
  try {
    await request('/users', {
      method: 'POST',
      body: JSON.stringify({
        username: userForm.username.trim(),
        display_name: userForm.display_name.trim(),
        role: userForm.role,
        password: userForm.password,
      }),
    })
    Object.assign(userForm, { username: '', display_name: '', role: 'operator', password: '' })
    showToast('success', '成员账号已创建')
    const results = await Promise.all([request('/users'), request('/audit-logs?limit=100')])
    users.value = results[0]
    auditLogs.value = results[1]
  } catch (error) {
    showToast('error', '账号创建失败', error.message)
  } finally {
    creatingUser.value = false
  }
}

function toggleWeekday(day) {
  taskForm.weekly_days = taskForm.weekly_days.includes(day)
    ? taskForm.weekly_days.filter((item) => item !== day)
    : [...taskForm.weekly_days, day].sort()
}

function toggleCreateWeekday(day) {
  appForm.weekly_days = appForm.weekly_days.includes(day)
    ? appForm.weekly_days.filter((item) => item !== day)
    : [...appForm.weekly_days, day].sort()
}

function resetFilters() {
  Object.assign(filters, { name: '', enabled: 'all', trigger_type: 'all' })
}

function triggerLabel(type) {
  return triggerOptions.find((item) => item.value === type)?.label || type
}

function triggerDetail(task) {
  const config = task.trigger_config || {}
  if (task.trigger_type === 'daily') return '每天 ' + config.time
  if (task.trigger_type === 'weekly') {
    const days = (config.weekdays || []).map((day) => weekdayOptions.find((item) => item.value === day)?.label).join('、')
    return '周' + days + ' ' + config.time
  }
  return '按需手动运行'
}

function sourceLabel(source) {
  return source === 'schedule' ? '定时调度' : '手动运行'
}

function statusLabel(status) {
  return ({
    idle: '尚未运行', pending: '排队中', running: '运行中', success: '成功',
    failed: '失败', timeout: '超时', cancelled: '已取消',
  })[status] || status || '未知'
}

function businessOutcomeLabel(outcome) {
  return ({
    success: '业务成功', failure: '业务失败', manual_required: '需要人工介入',
  })[outcome] || outcome || '—'
}

function envStatusLabel(status) {
  return ({
    ready: 'Python 就绪', building: '准备中', failed: '准备失败',
    pending: '准备中', not_built: '准备中',
  })[status] || status || '未知'
}

function notificationLabel(status) {
  return ({
    pending: '等待发送', sent: '已发送', skipped: '未配置',
    disabled: '已关闭', failed: '发送失败',
  })[status] || status || '—'
}

function roleLabel(role) {
  return role === 'admin' ? '管理员' : '普通成员'
}

function auditActionLabel(action) {
  const map = {
    login: '登录系统', login_failed: '登录失败', logout: '退出登录', change_password: '修改密码',
    create_task: '创建任务', update_task: '修改任务', delete_task: '删除任务',
    archive_task: '删除任务', run_task: '发起运行', cancel_execution: '取消执行',
    create_app: '创建任务', delete_app: '清理旧任务', remove_app: '清理旧任务', rebuild_app: '修复运行环境',
    rebuild_environment: '修复运行环境', create_user: '创建成员',
  }
  return map[action] || action || '系统操作'
}

function formatDuration(value) {
  if (value == null) return '—'
  const seconds = Math.max(0, Number(value) || 0) / 1000
  const display = seconds < 1
    ? seconds.toFixed(3)
    : seconds.toFixed(2).replace(/\.00$/, '').replace(/(\.\d)0$/, '$1')
  return display + '秒'
}

function formatFileSize(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '大小未知'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return size.toLocaleString('zh-CN', { maximumFractionDigits: unit ? 1 : 0 }) + ' ' + units[unit]
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' }) : '—'
}

function timestampOf(value) {
  const timestamp = value ? new Date(value).getTime() : 0
  return Number.isFinite(timestamp) ? timestamp : 0
}

function shanghaiDateKey(value) {
  const timestamp = timestampOf(value)
  if (!timestamp) return ''
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date(timestamp))
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}`
}

function formatClock(value) {
  return value ? new Date(value).toLocaleTimeString('zh-CN', {
    hour12: false, hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Shanghai',
  }) : '—'
}

function dateKeyParts(dateKey) {
  const [year, month, day] = String(dateKey || '').split('-').map(Number)
  return { year, month, day }
}

function addDaysToDateKey(dateKey, amount) {
  const { year, month, day } = dateKeyParts(dateKey)
  const date = new Date(Date.UTC(year, month - 1, day + amount))
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(date.getUTCDate()).padStart(2, '0')}`
}

function isoWeekday(dateKey) {
  const { year, month, day } = dateKeyParts(dateKey)
  return new Date(Date.UTC(year, month - 1, day)).getUTCDay() || 7
}

function scheduleTimestamp(dateKey, time) {
  const { year, month, day } = dateKeyParts(dateKey)
  const [hour, minute] = String(time || '').split(':').map(Number)
  if (![year, month, day, hour, minute].every(Number.isFinite)) return 0
  return Date.UTC(year, month - 1, day, hour - 8, minute)
}

function scheduleExecution(taskId, dateKey) {
  return executions.value
    .filter((item) => Number(item.task_id) === Number(taskId)
      && item.trigger_source === 'schedule'
      && shanghaiDateKey(item.created_at || item.started_at) === dateKey)
    .sort((a, b) => timestampOf(b.created_at || b.started_at) - timestampOf(a.created_at || a.started_at))[0] || null
}

function buildScheduleDay(dateKey) {
  const weekday = isoWeekday(dateKey)
  const entries = tasks.value.flatMap((task) => {
    if (!task.enabled || !['daily', 'weekly'].includes(task.trigger_type)) return []
    const config = task.trigger_config || {}
    if (task.trigger_type === 'weekly' && !(config.weekdays || []).map(Number).includes(weekday)) return []
    if (!/^\d{2}:\d{2}$/.test(config.time || '')) return []
    const plannedTimestamp = scheduleTimestamp(dateKey, config.time)
    if (!plannedTimestamp) return []
    const execution = scheduleExecution(task.id, dateKey)
    if (!execution && timestampOf(task.created_at) > plannedTimestamp) return []
    return [{
      task,
      dateKey,
      plannedTimestamp,
      plannedAt: new Date(plannedTimestamp).toISOString(),
      execution,
    }]
  }).sort((a, b) => a.plannedTimestamp - b.plannedTimestamp || String(a.task.name).localeCompare(String(b.task.name), 'zh-CN'))
  const { year, month, day } = dateKeyParts(dateKey)
  return {
    dateKey,
    weekday,
    weekdayLabel: weekdayOptions.find((item) => item.value === weekday)?.label || '',
    dateLabel: `${month}月${day}日`,
    fullDateLabel: `${year}年${month}月${day}日`,
    isToday: dateKey === shanghaiDateKey(Date.now()),
    entries,
  }
}

function scheduleEntryState(entry) {
  if (entry.execution) return entry.execution.status
  return entry.plannedTimestamp > Date.now() ? 'scheduled' : 'missed'
}

function scheduleEntryStateLabel(entry) {
  const state = scheduleEntryState(entry)
  if (state === 'scheduled') return '待执行'
  if (state === 'missed') return '未触发'
  return statusLabel(state)
}

function taskIsActive(task) {
  return activeExecutions.value.some((item) => Number(item.task_id) === Number(task.id))
}

function requesterName(item) {
  return item.requested_by_name || item.requested_by_username || '系统调度'
}

function auditActor(item) {
  return item.actor_name || item.user_display_name || item.username || '系统'
}

function auditTarget(item) {
  if (item.target_name) return item.target_name
  if (item.target_type && item.target_id) return item.target_type + ' #' + item.target_id
  if (item.target_type) return item.target_type
  if (item.resource_type && item.resource_id) return item.resource_type + ' #' + item.resource_id
  if (item.entity_type && item.entity_id) return item.entity_type + ' #' + item.entity_id
  return '—'
}

async function openExecution(item) {
  detail.value = { ...item }
  try {
    const result = await request('/executions/' + item.id)
    if (detail.value?.id === item.id) detail.value = result
  } catch (error) {
    if (error.status === 401) handleSessionExpired()
    else if (detail.value?.id === item.id) showToast('error', '无法载入完整日志', error.message)
  }
}

function resetArtifactDownload() {
  artifactDownloadGeneration += 1
  artifactDownloadController?.abort()
  artifactDownloadController = null
  Object.assign(artifactDownload, { busy: false, path: '', message: '', error: false })
}

async function downloadArtifact(file) {
  if (!detailFinished.value || artifactDownload.busy) return
  const executionId = detail.value.id
  const generation = artifactDownloadGeneration
  const controller = new AbortController()
  artifactDownloadController = controller
  Object.assign(artifactDownload, { busy: true, path: file.path, message: '', error: false })
  const url = API + '/executions/' + executionId + '/artifacts/download?path=' + encodeURIComponent(file.path)
  try {
    const response = await fetch(url, {
      method: 'HEAD', credentials: 'include', cache: 'no-store', signal: controller.signal,
    })
    if (generation !== artifactDownloadGeneration || detail.value?.id !== executionId) return
    if (response.status === 401) {
      handleSessionExpired()
      return
    }
    if (!response.ok) {
      throw new Error(({
        403: '你没有下载这个文件的权限。',
        404: '文件已不在原位置，请刷新运行记录后查看。',
        409: '任务还未结束，请在运行结束后下载。',
      })[response.status] || '文件暂时无法下载，请稍后重试。')
    }
    // 交给浏览器直接下载，避免把整个文件读入页面内存。
    const link = document.createElement('a')
    link.href = url
    link.download = file.name || ''
    document.body.appendChild(link)
    link.click()
    link.remove()
    artifactDownload.message = '下载请求已交给浏览器，请在浏览器下载列表查看。'
  } catch (error) {
    if (generation !== artifactDownloadGeneration || error.name === 'AbortError') return
    artifactDownload.error = true
    artifactDownload.message = error instanceof TypeError
      ? '暂时无法连接服务器，请检查网络后重试。'
      : error.message
  } finally {
    if (generation === artifactDownloadGeneration) {
      artifactDownload.busy = false
      artifactDownloadController = null
    }
  }
}

function handleKeydown(event) {
  if (event.key !== 'Escape') return
  taskModalOpen.value = false
  detail.value = null
  deletingTask.value = null
  if (!me.value?.must_change_password) changePasswordOpen.value = false
  toast.visible = false
}

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
  checkAuth()
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeydown)
  window.clearTimeout(pollTimer)
  window.clearTimeout(toastTimer)
  resetArtifactDownload()
})
</script>

<template>
  <div v-if="authChecking" class="auth-screen">
    <div class="auth-loading" role="status">
      <div class="brand-mark large" aria-hidden="true"><span></span><span></span><span></span></div>
      <span class="spinner"></span>
      <strong>正在连接共享任务中心…</strong>
    </div>
  </div>

  <div v-else-if="!me" class="auth-screen">
    <div class="auth-ornament auth-ornament-one"></div>
    <div class="auth-ornament auth-ornament-two"></div>
    <section class="login-card">
      <div class="login-brand">
        <div class="brand-mark large" aria-hidden="true"><span></span><span></span><span></span></div>
        <div><strong>SpiderFly</strong><small>团队 Python 自动化控制台</small></div>
      </div>
      <div class="login-copy">
        <span class="eyebrow">SHARED AUTOMATION</span>
        <h1>欢迎回来</h1>
        <p>统一管理任务、运行队列和历史记录。</p>
      </div>
      <form class="login-form" @submit.prevent="login">
        <label class="field">
          <span>账号</span>
          <input v-model="loginForm.username" type="text" autocomplete="username" autofocus placeholder="请输入用户名" />
        </label>
        <label class="field">
          <span>密码</span>
          <input v-model="loginForm.password" type="password" autocomplete="current-password" placeholder="请输入密码" />
        </label>
        <button class="button primary login-button" type="submit" :disabled="loginBusy">
          {{ loginBusy ? '正在登录…' : '登录共享任务中心' }}
        </button>
      </form>
      <div class="login-footnote"><span class="status-dot success"></span>Python、依赖和脚本都由共享电脑统一管理</div>
    </section>
  </div>

  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand-block">
        <div class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></div>
        <div><strong>SpiderFly</strong><small>Python 自动化控制台</small></div>
      </div>

      <nav class="sidebar-nav" aria-label="主导航">
        <button
          v-for="item in navItems"
          :key="item.id"
          type="button"
          :class="{ active: view === item.id }"
          :aria-current="view === item.id ? 'page' : undefined"
          @click="navigateTo(item.id)"
        >
          <span class="nav-mark">{{ item.mark }}</span>
          <span>{{ item.label }}</span>
          <i v-if="item.id === 'runtime' && activeExecutions.length">{{ activeExecutions.length }}</i>
        </button>
      </nav>

      <div class="local-mode-card">
        <span class="status-dot" :class="runningExecution ? 'running' : 'success'"></span>
        <div>
          <strong>{{ runningExecution ? '运行主机忙碌' : '运行主机在线' }}</strong>
          <small>{{ queuedExecutions.length ? queuedExecutions.length + ' 项正在排队' : '队列目前为空' }}</small>
        </div>
      </div>

      <div class="account-card">
        <span class="avatar">{{ (me.display_name || me.username).slice(0, 1) }}</span>
        <div><strong>{{ me.display_name || me.username }}</strong><small>{{ roleLabel(me.role) }}</small></div>
        <span class="account-actions">
          <button type="button" aria-label="修改密码" title="修改密码" @click="changePasswordOpen = true">✎</button>
          <button type="button" aria-label="退出登录" title="退出登录" @click="logout">↪</button>
        </span>
      </div>
      <div class="sidebar-footer">SPIDERFLY · PY CONTROL</div>
    </aside>

    <main class="workspace">
      <header class="workspace-header">
        <div>
          <span class="eyebrow">SPIDERFLY / PYTHON CONTROL CENTER</span>
          <h1>{{ pageTitle }}</h1>
        </div>
        <div class="header-actions">
          <span class="live-state"><i class="status-dot" :class="runningExecution ? 'running' : 'success'"></i>{{ runningExecution ? '正在运行 1 项' : '执行器空闲' }}</span>
          <button v-if="view === 'tasks'" class="button primary" type="button" @click="openCreate">
            <span aria-hidden="true">＋</span> 新建任务
          </button>
        </div>
      </header>

      <nav v-if="view === 'runtime'" class="center-tabs" aria-label="运行中心功能">
        <button v-for="tab in runtimeTabs" :key="tab.id" type="button" :class="{ active: runtimeTab === tab.id }" @click="runtimeTab = tab.id">
          {{ tab.label }}
          <i v-if="tab.id === 'queue' && activeExecutions.length">{{ activeExecutions.length }}</i>
        </button>
      </nav>
      <nav v-else-if="view === 'management' && isAdmin" class="center-tabs" aria-label="管理中心功能">
        <button v-for="tab in managementTabs" :key="tab.id" type="button" :class="{ active: managementTab === tab.id }" @click="managementTab = tab.id">{{ tab.label }}</button>
      </nav>

      <div v-if="loading" class="loading-panel" role="status">
        <span class="spinner"></span>正在载入共享任务…
      </div>

      <template v-else>
        <section v-if="view === 'overview'" class="view-stack">
          <div class="metric-grid">
            <article class="metric-card">
              <span>任务总数</span><strong>{{ tasks.length }}</strong><small>{{ tasks.filter((item) => item.enabled).length }} 个已启用</small>
            </article>
            <article class="metric-card">
              <span>Python 环境</span><strong>{{ readyTasks.length }}</strong><small>{{ buildingTasks.length ? buildingTasks.length + ' 个准备中' : '已就绪' }}</small>
            </article>
            <article class="metric-card featured">
              <span>运行队列</span><strong :class="{ 'queue-text': activeExecutions.length }">{{ activeExecutions.length }}</strong><small>{{ runningExecution ? '1 个运行，' + queuedExecutions.length + ' 个等待' : '当前没有运行任务' }}</small>
            </article>
            <article class="metric-card">
              <span>运行主机</span><strong>{{ runningExecution ? '忙碌' : '空闲' }}</strong><small>始终只运行一个 Python</small>
            </article>
          </div>

          <nav class="console-path" :class="{ 'operator-path': !isAdmin }" aria-label="常用入口">
            <template v-if="isAdmin">
              <button type="button" @click="navigateTo('management', 'apps')"><span>01</span><strong>创建任务</strong><small>上传脚本并准备独立环境</small></button>
              <i>→</i>
            </template>
            <button type="button" @click="navigateTo('tasks')"><span>{{ isAdmin ? '02' : '01' }}</span><strong>任务中心</strong><small>运行、定时、修复和删除</small></button>
            <i>→</i>
            <button type="button" @click="navigateTo('runtime', 'queue')"><span>{{ isAdmin ? '03' : '02' }}</span><strong>任务时间表</strong><small>查看今日和本周计划</small></button>
            <i>→</i>
            <button type="button" @click="navigateTo('runtime', 'executions')"><span>{{ isAdmin ? '04' : '03' }}</span><strong>运行记录</strong><small>查看结果与完整日志</small></button>
          </nav>

          <div v-if="attentionTasks.length" class="notice warning">
            <span class="notice-icon">!</span>
            <div><strong>{{ attentionTasks.length }} 个任务最近一次运行异常</strong><small>可在运行记录中查看完整日志和通知结果。</small></div>
            <button class="button ghost compact" type="button" @click="navigateTo('runtime', 'executions')">查看记录</button>
          </div>

          <div class="two-column queue-overview">
            <section class="panel">
              <header class="panel-heading">
                <div><h2>当前队列</h2><p>一个运行，其余按顺序等待，不会抢占共享电脑。</p></div>
                <button class="text-button" type="button" @click="navigateTo('runtime', 'queue')">查看时间表</button>
              </header>
              <div v-if="runningExecution || queuedExecutions.length" class="queue-list">
                <button v-if="runningExecution" type="button" class="queue-row current" @click="openExecution(runningExecution)">
                  <span class="queue-number"><i class="status-dot running"></i></span>
                  <span class="run-copy"><strong>{{ runningExecution.task_name }}</strong><small>{{ requesterName(runningExecution) }} 发起 · {{ formatTime(runningExecution.started_at) }}</small></span>
                  <span class="run-result"><strong>正在运行</strong><small>{{ formatDuration(runningExecution.duration_ms) }}</small></span>
                </button>
                <button v-for="item in queuedExecutions.slice(0, 5)" :key="item.id" type="button" class="queue-row" @click="openExecution(item)">
                  <span class="queue-number">{{ item.queue_position || '·' }}</span>
                  <span class="run-copy"><strong>{{ item.task_name }}</strong><small>{{ requesterName(item) }} 发起 · {{ formatTime(item.created_at) }}</small></span>
                  <span class="run-result"><strong>排队中</strong><small>{{ item.error_message || ('第 ' + (item.queue_position || '—') + ' 位') }}</small></span>
                </button>
              </div>
              <div v-else class="empty-state compact-empty">
                <strong>共享执行器正在等待</strong>
                <p>现在没有运行或排队的任务。</p>
              </div>
            </section>

            <section class="panel">
              <header class="panel-heading">
                <div><h2>最近完成</h2><p>显示团队最近提交的运行记录。</p></div>
                <button class="text-button" type="button" @click="navigateTo('runtime', 'executions')">全部记录</button>
              </header>
              <div v-if="recentExecutions.length" class="run-list">
                <button v-for="item in recentExecutions.slice(0, 5)" :key="item.id" type="button" @click="openExecution(item)">
                  <span class="status-dot" :class="item.status"></span>
                  <span class="run-copy"><strong>{{ item.task_name }}</strong><small>{{ requesterName(item) }} · {{ formatTime(item.created_at) }}</small></span>
                  <span class="run-result"><strong>{{ statusLabel(item.status) }}</strong><small>{{ formatDuration(item.duration_ms) }}</small></span>
                </button>
              </div>
              <div v-else class="empty-state compact-empty"><strong>还没有完成记录</strong><p>运行完成后会显示在这里。</p></div>
            </section>
          </div>
        </section>

        <section v-else-if="view === 'tasks'" class="view-stack">
          <section class="panel scheduler-toolbar">
            <div class="filter-bar">
              <label class="filter-field"><span>任务名称</span><input v-model="filters.name" type="search" placeholder="输入任务名称" /></label>
              <label class="filter-field"><span>启用状态</span><select v-model="filters.enabled"><option value="all">全部状态</option><option value="enabled">已启用</option><option value="disabled">已停用</option></select></label>
              <label class="filter-field"><span>触发方式</span><select v-model="filters.trigger_type"><option value="all">全部触发方式</option><option v-for="option in triggerOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
              <div class="filter-actions">
                <button class="icon-button toolbar-icon" type="button" title="刷新任务" aria-label="刷新任务" @click="loadAll({ quiet: true, includeAdmin: false })">↻</button>
                <button class="icon-button toolbar-icon" type="button" title="清空筛选" aria-label="清空筛选" @click="resetFilters">···</button>
              </div>
            </div>
          </section>

          <section class="panel table-panel">
            <header class="panel-heading"><div><h2>任务列表</h2><p>{{ filteredTasks.length }} 项</p></div></header>
            <div v-if="filteredTasks.length" class="table-wrap">
              <table>
                <thead><tr><th>任务</th><th>启用</th><th>触发方式</th><th>下次运行</th><th>最近状态</th><th class="align-right">操作</th></tr></thead>
                <tbody>
                  <tr v-for="task in filteredTasks" :key="task.id">
                    <td>
                      <div class="primary-cell">
                        <strong>{{ task.name }}</strong>
                        <small class="inline-state" :class="'env-' + task.environment_status" :title="task.environment_error || ''">
                          {{ envStatusLabel(task.environment_status) }}<template v-if="task.environment_status === 'failed' && task.environment_error"> · {{ task.environment_error }}</template>
                        </small>
                      </div>
                    </td>
                    <td><button class="plan-state" :class="{ enabled: task.enabled }" type="button" @click="toggleTask(task)"><i></i>{{ task.enabled ? '已启用' : '已停用' }}</button></td>
                    <td><div class="primary-cell"><strong>{{ triggerLabel(task.trigger_type) }}</strong><small>{{ triggerDetail(task) }}</small></div></td>
                    <td><span :class="{ muted: !task.next_run_at }">{{ task.enabled ? formatTime(task.next_run_at) : '停用后不调度' }}</span></td>
                    <td><span class="status-badge"><i class="status-dot" :class="task.last_status"></i>{{ statusLabel(task.last_status) }}</span></td>
                    <td class="align-right">
                      <div class="row-actions">
                        <button class="button primary compact" type="button" :disabled="!task.enabled || task.environment_status !== 'ready' || taskIsActive(task)" @click="runTask(task)">{{ taskIsActive(task) ? '进行中' : '运行' }}</button>
                        <button class="icon-button" type="button" :disabled="taskIsActive(task)" aria-label="编辑任务" :title="taskIsActive(task) ? '任务完成后才能编辑' : '编辑任务'" @click="openEdit(task)">✎</button>
                        <button v-if="isAdmin && task.environment_status === 'failed'" class="button secondary compact" type="button" @click="rebuildTaskEnvironment(task)">修复环境</button>
                        <button v-if="isAdmin" class="button ghost compact archive-button" type="button" aria-label="删除任务" title="同时删除程序、独立环境和运行记录" @click="deletingTask = task">删除</button>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else-if="tasks.length" class="empty-state"><strong>没有匹配的任务</strong><p>调整筛选条件后再试。</p><button class="button secondary" type="button" @click="resetFilters">清空筛选</button></div>
            <div v-else class="empty-state"><div class="empty-canopy"><span></span><span></span><span></span></div><strong>还没有任务</strong></div>
          </section>
        </section>

        <section v-else-if="view === 'runtime' && runtimeTab === 'queue'" class="view-stack">
          <section class="panel task-schedule-panel">
            <header class="panel-heading schedule-panel-heading">
              <div>
                <h2>任务时间表</h2>
                <p>北京时间 · {{ manualTasks.length }} 个手动任务按需运行</p>
              </div>
              <div class="schedule-heading-actions">
                <div class="schedule-scope-switch" role="group" aria-label="时间范围">
                  <button type="button" :class="{ active: scheduleScope === 'today' }" @click="scheduleScope = 'today'">今日</button>
                  <button type="button" :class="{ active: scheduleScope === 'week' }" @click="scheduleScope = 'week'">本周</button>
                </div>
                <button class="button secondary compact" type="button" @click="loadAll({ quiet: true, includeAdmin: false })">刷新</button>
              </div>
            </header>
            <div v-if="scheduleScope === 'today'" class="schedule-today-view">
              <header class="schedule-date-heading">
                <div><strong>今天</strong><span>{{ taskScheduleDays[0]?.fullDateLabel }}</span></div>
                <small>{{ taskScheduleDays[0]?.entries.length || 0 }} 项自动任务</small>
              </header>
              <div v-if="taskScheduleDays[0]?.entries.length" class="schedule-agenda">
                <article v-for="entry in taskScheduleDays[0].entries" :key="entry.dateKey + '-' + entry.task.id" class="schedule-agenda-row" :class="'state-' + scheduleEntryState(entry)">
                  <time>{{ formatClock(entry.plannedAt) }}</time>
                  <span class="schedule-agenda-marker" aria-hidden="true"><i></i></span>
                  <div class="schedule-entry-card" :class="{ clickable: entry.execution }" :role="entry.execution ? 'button' : undefined" :tabindex="entry.execution ? 0 : undefined" @click="entry.execution && openExecution(entry.execution)" @keydown.enter="entry.execution && openExecution(entry.execution)">
                    <span><strong>{{ entry.task.name }}</strong><small>{{ entry.task.trigger_type === 'daily' ? '每日任务' : '每周任务' }}</small></span>
                    <span class="schedule-state"><i></i>{{ scheduleEntryStateLabel(entry) }}</span>
                  </div>
                </article>
              </div>
              <div v-else class="schedule-empty"><strong>今天没有自动任务</strong><span>手动任务仍可在任务中心按需运行。</span></div>
            </div>

            <div v-else class="schedule-week-scroll">
              <div class="schedule-week-grid">
                <section v-for="day in taskScheduleDays" :key="day.dateKey" class="schedule-week-day" :class="{ today: day.isToday }">
                  <header><span>周{{ day.weekdayLabel }}</span><strong>{{ day.dateLabel }}</strong></header>
                  <div v-if="day.entries.length" class="schedule-week-list">
                    <article v-for="entry in day.entries" :key="entry.dateKey + '-' + entry.task.id" class="schedule-week-entry" :class="['state-' + scheduleEntryState(entry), { clickable: entry.execution }]" :role="entry.execution ? 'button' : undefined" :tabindex="entry.execution ? 0 : undefined" @click="entry.execution && openExecution(entry.execution)" @keydown.enter="entry.execution && openExecution(entry.execution)">
                      <time>{{ formatClock(entry.plannedAt) }}</time>
                      <strong>{{ entry.task.name }}</strong>
                      <small><span class="schedule-state"><i></i>{{ scheduleEntryStateLabel(entry) }}</span></small>
                    </article>
                  </div>
                  <div v-else class="schedule-week-empty">无自动任务</div>
                </section>
              </div>
            </div>
          </section>
        </section>

        <section v-else-if="view === 'runtime' && runtimeTab === 'executions'" class="view-stack">
          <div class="section-summary record-summary">
            <p>这里保留每次 Python 运行的最终状态、发起人、耗时、通知结果以及 stdout / stderr 日志。</p>
            <span>{{ completedExecutions.length }} 条记录</span>
          </div>
          <section class="panel table-panel">
            <header class="panel-heading">
              <div><h2>运行记录</h2><p>最近 {{ completedExecutions.length }} 条已完成记录，点击任意一行查看完整日志。</p></div>
              <button class="button secondary compact" type="button" @click="loadAll({ quiet: true, includeAdmin: false })">刷新记录</button>
            </header>
            <div v-if="completedExecutions.length" class="table-wrap">
              <table>
                <thead><tr><th>最终状态</th><th>任务</th><th>发起人</th><th>触发来源</th><th>提交时间</th><th>完成时间</th><th>耗时</th><th>通知</th><th class="align-right">操作</th></tr></thead>
                <tbody>
                  <tr v-for="item in completedExecutions" :key="item.id" class="clickable-row" tabindex="0" @click="openExecution(item)" @keydown.enter="openExecution(item)">
                    <td><span class="status-badge"><i class="status-dot" :class="item.status"></i>{{ statusLabel(item.status) }}</span></td>
                    <td><div class="primary-cell"><strong>{{ item.task_name }}</strong><small>运行编号 #{{ item.id }}</small></div></td>
                    <td><span class="person-chip">{{ requesterName(item).slice(0, 1) }}</span>{{ requesterName(item) }}</td>
                    <td>{{ sourceLabel(item.trigger_source) }}</td>
                    <td>{{ formatTime(item.created_at) }}</td>
                    <td>{{ formatTime(item.ended_at || item.finished_at || item.completed_at) }}</td>
                    <td>{{ formatDuration(item.duration_ms) }}</td>
                    <td>{{ notificationLabel(item.notification_status) }}</td>
                    <td class="align-right"><button class="text-button" type="button" @click.stop="openExecution(item)">查看日志</button></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="empty-state"><strong>还没有运行记录</strong><p>任务完成后会自动归档到这里。</p><button class="button secondary" type="button" @click="navigateTo('tasks')">打开任务中心</button></div>
          </section>
        </section>

        <section v-else-if="view === 'management' && managementTab === 'apps' && isAdmin" class="view-stack">
          <section v-if="isAdmin" class="panel">
            <form class="app-upload-form" @submit.prevent="uploadApp">
              <label class="field upload-name"><span>任务名称</span><input v-model="appForm.name" type="text" maxlength="100" placeholder="例如：财务日报" /></label>
              <label class="field upload-description"><span>任务说明（可不填）</span><input v-model="appForm.description" type="text" maxlength="500" placeholder="这个任务负责什么" /></label>
              <label class="field file-field upload-script">
                <span>Python 脚本</span>
                <input :key="uploadKey" type="file" accept=".py,text/x-python" @change="selectScript" />
              </label>
              <label class="field file-field upload-requirements">
                <span>Python 依赖（第三方库才需要）</span>
                <input :key="'requirements-' + uploadKey" type="file" accept=".txt,text/plain" @change="selectRequirementsFile" />
              </label>
              <details class="manual-requirements">
                <summary>{{ appForm.requirements_filename ? '已读取依赖，可展开修改' : '没有 requirements.txt？手动填写' }}</summary>
                <label class="field"><textarea v-model="appForm.requirements_text" rows="4" maxlength="20000" placeholder="每行一个，例如：&#10;DrissionPage==4.1.1.4&#10;pandas==2.3.2"></textarea></label>
              </details>
              <label class="field file-field upload-template">
                <span>Excel 模板（可不选）</span>
                <input :key="'template-' + uploadKey" type="file" accept=".xlsx,.xlsm,.xltx,.xltm" @change="selectTemplate" />
              </label>
              <section class="schedule-card create-task-settings">
                <div class="schedule-card-heading">
                  <div><strong>运行设置</strong><small>创建时一次设置完成，以后需要变更时再编辑。</small></div>
                  <span>北京时间 · 最长 10 分钟</span>
                </div>
                <div class="trigger-choice-grid">
                  <button v-for="option in triggerOptions" :key="option.value" type="button" :class="{ active: appForm.trigger_type === option.value }" @click="appForm.trigger_type = option.value"><i></i>{{ option.label }}</button>
                </div>
                <div v-if="appForm.trigger_type === 'manual'" class="schedule-hint">创建后由伙伴点击“运行”，不会自动执行。</div>
                <label v-else-if="appForm.trigger_type === 'daily'" class="field"><span>每天执行时间</span><input v-model="appForm.daily_time" type="time" /></label>
                <div v-else class="weekly-fields">
                  <div class="field"><span>执行星期</span><div class="weekday-grid"><button v-for="day in weekdayOptions" :key="day.value" type="button" :class="{ active: appForm.weekly_days.includes(day.value) }" @click="toggleCreateWeekday(day.value)">周{{ day.label }}</button></div></div>
                  <label class="field"><span>执行时间</span><input v-model="appForm.weekly_time" type="time" /></label>
                </div>
                <div class="create-settings-bottom">
                  <div class="field create-plan-state">
                    <span>任务状态</span>
                    <button class="switch-row" type="button" role="switch" :aria-checked="appForm.enabled" @click="appForm.enabled = !appForm.enabled"><i :class="{ active: appForm.enabled }"><b></b></i><span>{{ appForm.enabled ? '创建后启用' : '暂不启用' }}</span></button>
                  </div>
                  <div class="notification-options">
                    <div><strong>最终通知</strong><small>每次运行结束后最多发送一条。</small></div>
                    <label><input v-model="appForm.notify_on_success" type="checkbox" />成功时通知</label>
                    <label><input v-model="appForm.notify_on_failure" type="checkbox" />失败时通知</label>
                  </div>
                </div>
              </section>
              <div class="upload-actions"><button class="button primary" type="submit" :disabled="uploading">{{ uploading ? '正在创建…' : '创建任务' }}</button></div>
            </form>
          </section>
        </section>

        <section v-else-if="view === 'management' && managementTab === 'settings' && isAdmin" class="view-stack settings-grid">
          <section class="panel">
            <header class="panel-heading"><div><h2>共享运行方式</h2><p>所有成员连接同一个网页，由这台电脑统一执行。</p></div><span class="mini-badge success-badge">{{ settings.scheduler === 'running' ? '运行中' : '已连接' }}</span></header>
            <div class="setting-rows">
              <div><span>执行数量</span><strong>1 at a time</strong><small>任何时候最多运行一个 Python，其余任务进入持久队列。</small></div>
              <div><span>调度时区</span><strong>{{ settings.scheduler_timezone || 'Asia/Shanghai' }}</strong><small>所有计划按北京时间计算。</small></div>
              <div><span>任务环境</span><strong>one task · one venv</strong><small>每个任务绑定自己的程序环境，互不共用。</small></div>
              <div><span>最长运行</span><strong>10 分钟</strong><small>只计算 Python 实际运行时间，排队等待不计时。</small></div>
              <div><span>公共文件夹</span><strong>{{ settings.work_directory_name || '共享工作区' }}</strong><small>运行前、运行后都会自动清空；模板每次重新复制。</small></div>
              <div><span>运行前检查</span><strong>Excel / 端口 {{ settings.managed_browser_port || 9123 }}</strong><small>管理用 Chrome 可以保持打开；只在专用自动化浏览器未退出时排队。</small></div>
              <div><span>轮询刷新</span><strong>1.2s / 5s</strong><small>繁忙时快速刷新，空闲时降低请求频率。</small></div>
            </div>
          </section>
          <section class="panel">
            <header class="panel-heading"><div><h2>我的账号</h2><p>当前登录信息与账号安全。</p></div><span class="mini-badge neutral-badge">{{ roleLabel(me.role) }}</span></header>
            <div class="profile-block">
              <span class="avatar large-avatar">{{ (me.display_name || me.username).slice(0, 1) }}</span>
              <div><strong>{{ me.display_name || me.username }}</strong><small>@{{ me.username }}</small></div>
            </div>
            <div class="profile-actions">
              <button class="button secondary" type="button" @click="changePasswordOpen = true">修改密码</button>
              <button class="button ghost" type="button" @click="logout">退出登录</button>
            </div>
          </section>
          <section class="panel">
            <header class="panel-heading"><div><h2>飞书通知</h2><p>凭据只保存在后端，不会显示在网页中。</p></div><span class="mini-badge" :class="settings.feishu_configured ? 'success-badge' : 'warning-badge'">{{ settings.feishu_configured ? '已配置' : '待配置' }}</span></header>
            <div class="policy-list">
              <div><span class="policy-index success-bg">01</span><p><strong>运行成功</strong><small>发送任务名称、成功状态和耗时。</small></p></div>
              <div><span class="policy-index danger-bg">02</span><p><strong>运行失败</strong><small>发送错误摘要和最终结果。</small></p></div>
              <div><span class="policy-index neutral-bg">03</span><p><strong>安静通知</strong><small>不发送开始提醒，结束时最多一条。</small></p></div>
            </div>
          </section>
        </section>

        <section v-else-if="view === 'management' && managementTab === 'users' && isAdmin" class="view-stack">
          <section class="panel">
            <header class="panel-heading"><div><h2>创建成员账号</h2><p>每个人使用独立账号，运行和修改操作都会留下记录。</p></div><span class="mini-badge neutral-badge">管理员区域</span></header>
            <form class="user-create-form" @submit.prevent="createUser">
              <label class="field"><span>登录账号</span><input v-model="userForm.username" type="text" autocomplete="off" maxlength="50" placeholder="例如：xiaoming" /></label>
              <label class="field"><span>显示名称</span><input v-model="userForm.display_name" type="text" maxlength="100" placeholder="例如：小明" /></label>
              <label class="field"><span>成员角色</span><select v-model="userForm.role"><option value="operator">普通成员</option><option value="admin">管理员</option></select></label>
              <label class="field"><span>初始密码</span><input v-model="userForm.password" type="password" minlength="10" autocomplete="new-password" placeholder="至少 10 个字符" /></label>
              <button class="button primary" type="submit" :disabled="creatingUser">{{ creatingUser ? '正在创建…' : '创建成员' }}</button>
            </form>
          </section>
          <section class="panel table-panel">
            <header class="panel-heading"><div><h2>团队成员</h2><p>共 {{ users.length }} 个账号</p></div></header>
            <div v-if="users.length" class="member-grid">
              <article v-for="user in users" :key="user.id" class="member-card">
                <span class="avatar">{{ (user.display_name || user.username).slice(0, 1) }}</span>
                <div><strong>{{ user.display_name || user.username }}</strong><small>@{{ user.username }}</small></div>
                <span class="mini-badge" :class="user.role === 'admin' ? 'success-badge' : 'neutral-badge'">{{ roleLabel(user.role) }}</span>
              </article>
            </div>
            <div v-else class="empty-state compact-empty"><strong>暂无成员记录</strong></div>
          </section>
        </section>

        <section v-else-if="view === 'management' && managementTab === 'audit' && isAdmin" class="view-stack">
          <div class="section-summary"><p>这里记录谁在什么时间登录、修改任务、发起运行或管理自动化程序，方便团队追溯操作。</p></div>
          <section class="panel table-panel">
            <header class="panel-heading"><div><h2>最近操作</h2><p>最近 {{ auditLogs.length }} 条审计记录</p></div><button class="button secondary compact" type="button" @click="loadAll({ quiet: true, includeAdmin: true })">刷新记录</button></header>
            <div v-if="auditLogs.length" class="table-wrap">
              <table>
                <thead><tr><th>时间</th><th>操作人</th><th>操作</th><th>对象</th><th>说明</th></tr></thead>
                <tbody>
                  <tr v-for="item in auditLogs" :key="item.id">
                    <td>{{ formatTime(item.created_at) }}</td>
                    <td><span class="person-chip">{{ auditActor(item).slice(0, 1) }}</span>{{ auditActor(item) }}</td>
                    <td><strong class="audit-action">{{ auditActionLabel(item.action) }}</strong></td>
                    <td>{{ auditTarget(item) }}</td>
                    <td class="audit-detail">{{ item.summary || item.detail || item.description || item.message || '—' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="empty-state compact-empty"><strong>暂无审计记录</strong><p>团队操作发生后会显示在这里。</p></div>
          </section>
        </section>
      </template>
    </main>

    <div v-if="taskModalOpen" class="modal-layer" @mousedown.self="taskModalOpen = false">
      <section class="modal plan-modal" role="dialog" aria-modal="true" aria-label="编辑任务">
        <header><div><span class="eyebrow">TEAM TASK</span><h2>编辑任务</h2></div><button class="modal-close" type="button" aria-label="关闭" @click="taskModalOpen = false">×</button></header>
        <div class="modal-body">
          <label class="field"><span>任务名称</span><input v-model="taskForm.name" type="text" maxlength="100" placeholder="例如：订单同步" /></label>
          <label class="field"><span>任务说明</span><input v-model="taskForm.description" type="text" maxlength="500" placeholder="这个计划负责什么" /></label>

          <section class="schedule-card">
            <div class="schedule-card-heading"><div><strong>触发方式</strong><small>支持手动、每日和每周三种方式。</small></div><span>北京时间 UTC+8</span></div>
            <div class="trigger-choice-grid">
              <button v-for="option in triggerOptions" :key="option.value" type="button" :class="{ active: taskForm.trigger_type === option.value }" @click="taskForm.trigger_type = option.value"><i></i>{{ option.label }}</button>
            </div>
            <div v-if="taskForm.trigger_type === 'manual'" class="schedule-hint">仅通过“运行”按钮提交，不自动执行。</div>
            <label v-else-if="taskForm.trigger_type === 'daily'" class="field"><span>每天执行时间</span><input v-model="taskForm.daily_time" type="time" /></label>
            <div v-else class="weekly-fields">
              <div class="field"><span>执行星期</span><div class="weekday-grid"><button v-for="day in weekdayOptions" :key="day.value" type="button" :class="{ active: taskForm.weekly_days.includes(day.value) }" @click="toggleWeekday(day.value)">周{{ day.label }}</button></div></div>
              <label class="field"><span>执行时间</span><input v-model="taskForm.weekly_time" type="time" /></label>
            </div>
          </section>

          <div class="field-grid">
            <div class="field"><span>最长运行时间</span><div class="schedule-hint">固定为 10 分钟，从 Python 真正开始运行时计算；排队等待不计时。</div></div>
            <div class="field"><span>计划状态</span><button class="switch-row" type="button" role="switch" :aria-checked="taskForm.enabled" @click="taskForm.enabled = !taskForm.enabled"><i :class="{ active: taskForm.enabled }"><b></b></i><span>{{ taskForm.enabled ? '保存后立即启用' : '暂不启用' }}</span></button></div>
          </div>
          <div class="notification-options">
            <div><strong>最终通知</strong><small>每次运行结束后最多发送一条。</small></div>
            <label><input v-model="taskForm.notify_on_success" type="checkbox" />成功时通知</label>
            <label><input v-model="taskForm.notify_on_failure" type="checkbox" />失败时通知</label>
          </div>
          <div v-if="editingTask" class="edit-version-note">当前版本 {{ editingTask.version }} · 如果其他成员已经修改，保存时会提醒你刷新。</div>
        </div>
        <footer><button class="button ghost" type="button" @click="taskModalOpen = false">取消</button><button class="button primary" type="button" :disabled="saving" @click="saveTask">{{ saving ? '正在保存…' : '保存修改' }}</button></footer>
      </section>
    </div>

    <div v-if="detail" class="modal-layer" @mousedown.self="detail = null">
      <section class="modal log-modal" role="dialog" aria-modal="true" aria-label="执行日志">
        <header><div><span class="eyebrow">EXECUTION #{{ detail.id }}</span><h2>{{ detail.task_name }}</h2></div><button class="modal-close" type="button" aria-label="关闭" @click="detail = null">×</button></header>
        <div class="execution-summary">
          <span class="status-badge"><i class="status-dot" :class="detail.status"></i>{{ statusLabel(detail.status) }}</span>
          <span v-if="detail.status === 'pending'">队列 <strong>第 {{ detail.queue_position || '—' }} 位</strong></span>
          <span>发起人 <strong>{{ requesterName(detail) }}</strong></span>
          <span>耗时 <strong>{{ formatDuration(detail.duration_ms) }}</strong></span>
          <span>退出码 <code>{{ detail.exit_code == null ? '—' : detail.exit_code }}</code></span>
          <span v-if="detail.business_outcome">脚本结果 <strong>{{ businessOutcomeLabel(detail.business_outcome) }}</strong></span>
          <span>飞书 <strong>{{ notificationLabel(detail.notification_status) }}</strong></span>
        </div>
        <div class="modal-body log-body">
          <div v-if="detail.status === 'pending' && detail.error_message" class="notice info"><span class="notice-icon">i</span><div><strong>仍在排队，没有开始计时</strong><small>{{ detail.error_message }}</small></div></div>
          <div v-if="detail.result_source === 'result_json'" class="notice" :class="detail.business_outcome === 'success' ? 'info' : 'warning'">
            <span class="notice-icon">{{ detail.business_outcome === 'success' ? '✓' : '!' }}</span>
            <div>
              <strong>{{ businessOutcomeLabel(detail.business_outcome) }} · {{ detail.result_code || '未提供编码' }}</strong>
              <small>{{ detail.result_message || '脚本未提供结果说明' }}</small>
              <small v-if="detail.retryable != null">建议重试：{{ detail.retryable ? '是' : '否' }}</small>
              <small v-if="detail.manual_code">人工处理编码：{{ detail.manual_code }}</small>
              <small v-if="detail.manual_action_url"><a :href="detail.manual_action_url" target="_blank" rel="noopener noreferrer">打开人工处理链接</a></small>
            </div>
          </div>
          <div class="execution-artifacts">
            <div class="artifact-heading"><span class="log-label">本次文件</span><small v-if="detailFinished && artifactFiles.length">{{ artifactFiles.length }} 个文件</small></div>
            <p v-if="!detailFinished" class="artifact-empty">运行结束后，可在这里下载本次保存的文件。</p>
            <template v-else-if="detail.artifacts">
              <ul v-if="artifactFiles.length" class="artifact-list">
                <li v-for="file in artifactFiles" :key="file.path" class="artifact-row">
                  <div class="artifact-info"><span class="artifact-path">{{ file.path }}</span><small>{{ formatFileSize(file.size_bytes) }}</small></div>
                  <button class="button secondary compact" type="button" :disabled="artifactDownload.busy" :aria-label="'下载 ' + file.path" @click="downloadArtifact(file)">{{ artifactDownload.busy && artifactDownload.path === file.path ? '准备下载…' : '下载' }}</button>
                </li>
              </ul>
              <p v-else-if="!detail.artifacts.error" class="artifact-empty">这次运行没有保存可下载的文件。</p>
              <p v-if="detail.artifacts.error" class="artifact-message danger-text">暂时无法完整读取本次文件，请稍后刷新。</p>
              <p v-if="detail.artifacts.truncated" class="artifact-message">文件较多，当前仅显示部分文件。</p>
            </template>
            <p v-else class="artifact-empty">正在读取本次文件…</p>
            <p v-if="artifactDownload.message" class="artifact-message" :class="{ 'danger-text': artifactDownload.error }" role="status">{{ artifactDownload.message }}</p>
          </div>
          <div><span class="log-label">标准输出 stdout</span><pre>{{ detail.stdout || (['pending', 'running'].includes(detail.status) ? '等待程序输出…' : '（无输出）') }}</pre></div>
          <div v-if="detail.stderr || (detail.error_message && detail.status !== 'pending')"><span class="log-label danger-text">错误输出 stderr</span><pre class="error-log">{{ detail.stderr || detail.error_message }}</pre></div>
          <div v-if="detail.notification_error" class="notice warning"><span class="notice-icon">!</span><div><strong>飞书通知未发送</strong><small>{{ detail.notification_error }}</small></div></div>
        </div>
        <footer>
          <button v-if="detail.status === 'pending'" class="button danger" type="button" @click="cancelExecution(detail)">取消排队</button>
          <button class="button secondary" type="button" @click="detail = null">关闭日志</button>
        </footer>
      </section>
    </div>

    <div v-if="deletingTask && isAdmin" class="modal-layer" @mousedown.self="deletingTask = null">
      <section class="modal confirm-modal" role="alertdialog" aria-modal="true" aria-label="删除任务">
        <header><div><span class="eyebrow danger-text">DELETE TASK</span><h2>删除“{{ deletingTask.name }}”</h2></div><button class="modal-close" type="button" aria-label="关闭" @click="deletingTask = null">×</button></header>
        <div class="modal-body confirm-copy">
          <p><strong>删除后无法恢复。</strong></p>
          <ul><li>删除任务和全部运行记录</li><li>删除 Python 脚本、模板、依赖和独立 .venv</li></ul>
        </div>
        <footer><button class="button ghost" type="button" @click="deletingTask = null">取消</button><button class="button danger" type="button" @click="confirmDelete">确认彻底删除</button></footer>
      </section>
    </div>

    <div v-if="changePasswordOpen" class="modal-layer mandatory-layer" @mousedown.self="!me.must_change_password && (changePasswordOpen = false)">
      <section class="modal password-modal" role="dialog" aria-modal="true" aria-label="修改密码">
        <header>
          <div><span class="eyebrow">ACCOUNT SECURITY</span><h2>{{ me.must_change_password ? '首次登录，请设置新密码' : '修改登录密码' }}</h2></div>
          <button v-if="!me.must_change_password" class="modal-close" type="button" aria-label="关闭" @click="changePasswordOpen = false">×</button>
        </header>
        <div class="modal-body">
          <div v-if="me.must_change_password" class="notice info"><span class="notice-icon">i</span><div><strong>为了账号安全，需要先修改初始密码</strong><small>完成后即可正常使用共享任务中心。</small></div></div>
          <label class="field"><span>当前密码</span><input v-model="passwordForm.current_password" type="password" autocomplete="current-password" /></label>
          <label class="field"><span>新密码</span><input v-model="passwordForm.new_password" type="password" minlength="10" autocomplete="new-password" /><small>至少 10 个字符。</small></label>
          <label class="field"><span>再次输入新密码</span><input v-model="passwordForm.confirm_password" type="password" minlength="10" autocomplete="new-password" /></label>
        </div>
        <footer>
          <button v-if="me.must_change_password" class="button ghost" type="button" @click="logout">退出登录</button>
          <button v-else class="button ghost" type="button" @click="changePasswordOpen = false">取消</button>
          <button class="button primary" type="button" :disabled="saving" @click="changePassword">{{ saving ? '正在保存…' : '保存新密码' }}</button>
        </footer>
      </section>
    </div>

    <div v-if="toast.visible" class="toast" :class="toast.type" role="status">
      <span class="toast-icon">{{ toast.type === 'success' ? '✓' : '!' }}</span>
      <span><strong>{{ toast.title }}</strong><small v-if="toast.message">{{ toast.message }}</small></span>
      <button type="button" aria-label="关闭通知" @click="toast.visible = false">×</button>
    </div>
  </div>
</template>
