import { computed, reactive, ref } from 'vue'
import { triggerConfig, taskOwnerKey } from '../../lib/format'
import { triggerOptions } from '../../lib/constants'
import { request } from '../../lib/api'

function manualRequestId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return 'task-manual-' + [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

export function createTasksState() {
  const tasks = ref([])
  const taskModalOpen = ref(false)
  const editingTask = ref(null)
  const deletingTask = ref(null)
  const targetHosts = ref([])
  const targetHostsLoading = ref(false)
  const filters = reactive({ name: '', enabled: 'all', trigger_type: 'all', owner: 'all' })
  const taskForm = reactive({
    name: '',
    description: '',
    app_id: '',
    timeout_seconds: 600,
    enabled: true,
    notify_on_success: true,
    notify_on_failure: true,
    failure_screenshot: false,
    trigger_type: 'manual',
    target_host_id: '',
    daily_time: '09:00',
    weekly_days: [1],
    weekly_time: '09:00',
    cron_expression: '0 9 * * *',
  })
  return { tasks, taskModalOpen, editingTask, deletingTask, targetHosts, targetHostsLoading, filters, taskForm }
}

export function useTasks({
  deletingTask,
  detail,
  editingTask,
  executions,
  filters,
  isAdmin,
  loadAll,
  handleSessionExpired,
  me,
  navigateTo,
  openExecution,
  remoteRunTarget,
  saving,
  showToast,
  taskForm,
  taskIsActive,
  taskModalOpen,
  tasks,
  targetHosts,
  targetHostsLoading,
}) {
  const pendingRuns = new Map()
  let sessionGeneration = 0
  let editorGeneration = 0
  const controllers = new Set()

  function operation() {
    const owner = me.value
    const generation = sessionGeneration
    return () => Boolean(owner) && me.value === owner && generation === sessionGeneration
  }
  async function api(path, options = {}) {
    const controller = new AbortController()
    controllers.add(controller)
    try { return await request(path, { ...options, signal: controller.signal }) }
    finally { controllers.delete(controller) }
  }
  function reportError(error, title) {
    if (error.name === 'AbortError') return
    if (error.status === 401) handleSessionExpired?.()
    else showToast('error', title, error.message)
  }
  function resetTaskSession() {
    sessionGeneration++
    editorGeneration++
    for (const controller of controllers) controller.abort()
    controllers.clear()
    pendingRuns.clear()
    targetHosts.value = []
    targetHostsLoading.value = false
    saving.value = false
    taskModalOpen.value = false
    editingTask.value = null
    deletingTask.value = null
    resetFilters()
    Object.assign(taskForm, { name: '', description: '', app_id: '', target_host_id: '', trigger_type: 'manual', daily_time: '09:00', weekly_time: '09:00', weekly_days: [1], cron_expression: '0 9 * * *' })
  }
  const manualTasks = computed(() =>
    tasks.value.filter((task) => task.enabled && task.trigger_type === 'manual'),
  )

  const attentionTasks = computed(() =>
    tasks.value.filter((item) => ['failed', 'timeout'].includes(item.last_status)),
  )

  const readyTasks = computed(() => tasks.value.filter((item) => item.environment_status === 'ready'))

  const buildingTasks = computed(() =>
    tasks.value.filter((item) => ['pending', 'building', 'not_built'].includes(item.environment_status)),
  )

  const ownerOptions = computed(() => {
    const owners = new Map()
    for (const task of tasks.value) {
      const value = taskOwnerKey(task)
      if (owners.has(value)) continue
      const name = String(task.created_by_name || '').trim() || '系统迁移'
      const isCurrentUser = Number(task.created_by) === Number(me.value?.id)
      owners.set(value, { value, label: isCurrentUser ? `${name}（我）` : name })
    }
    const currentUserValue = me.value?.id ? `user:${me.value.id}` : ''
    return [...owners.values()].sort((left, right) => {
      if (left.value === currentUserValue) return -1
      if (right.value === currentUserValue) return 1
      if (left.value === 'legacy') return 1
      if (right.value === 'legacy') return -1
      return left.label.localeCompare(right.label, 'zh-CN')
    })
  })

  const filteredTasks = computed(() =>
    tasks.value.filter((task) => {
      const taskName = String(task.name || '').toLowerCase()
      const nameMatch = !filters.name.trim() || taskName.includes(filters.name.trim().toLowerCase())
      const enabledMatch =
        filters.enabled === 'all' || Boolean(task.enabled) === (filters.enabled === 'enabled')
      const triggerMatch = filters.trigger_type === 'all' || task.trigger_type === filters.trigger_type
      const ownerMatch = filters.owner === 'all' || taskOwnerKey(task) === filters.owner
      return nameMatch && enabledMatch && triggerMatch && ownerMatch
    }),
  )

  function openCreate() {
    if (isAdmin.value) {
      navigateTo('management', 'apps')
      return
    }
    showToast('error', '请联系管理员创建任务')
  }

  function openEdit(task) {
    if (!me.value || saving.value) return
    if (taskIsActive(task)) {
      showToast('error', '任务正在排队或运行', '当前只能停用任务，完成后才能修改其他内容')
      return
    }
    editingTask.value = task
    editorGeneration++
    const config = task.trigger_config || {}
    Object.assign(taskForm, {
      name: task.name || '',
      description: task.description || '',
      app_id: task.app_id || '',
      timeout_seconds: 600,
      enabled: Boolean(task.enabled),
      notify_on_success: Boolean(task.notify_on_success),
      notify_on_failure: Boolean(task.notify_on_failure),
      failure_screenshot: Boolean(task.failure_screenshot),
      trigger_type: triggerOptions.some((item) => item.value === task.trigger_type)
        ? task.trigger_type
        : 'manual',
      target_host_id: task.target_host_id ? String(task.target_host_id) : '',
      daily_time: config.time || '09:00',
      weekly_days: [...(config.weekdays || [1])],
      weekly_time: config.time || '09:00',
      cron_expression: task.trigger_type === 'cron' ? config.expression || '' : '0 9 * * *',
    })
    taskModalOpen.value = true
    loadTargetHosts()
  }

  async function loadTargetHosts() {
    if (!isAdmin.value || targetHostsLoading.value) return
    targetHostsLoading.value = true
    const current = operation()
    try {
      const result = await api('/hosts')
      if (current()) targetHosts.value = result
    } catch (error) {
      if (current()) reportError(error, '宿主机列表载入失败')
    } finally {
      if (current()) targetHostsLoading.value = false
    }
  }

  function taskPayload() {
    const payload = {
      name: taskForm.name.trim(),
      description: taskForm.description.trim(),
      timeout_seconds: 600,
      enabled: taskForm.enabled,
      notify_on_success: taskForm.notify_on_success,
      notify_on_failure: taskForm.notify_on_failure,
      failure_screenshot: taskForm.failure_screenshot,
      trigger_type: taskForm.trigger_type,
      trigger_config: triggerConfig(taskForm),
    }
    if (isAdmin.value) payload.target_host_id = taskForm.target_host_id ? Number(taskForm.target_host_id) : null
    if (editingTask.value) payload.version = editingTask.value.version
    return payload
  }

  async function saveTask() {
    if (!me.value || saving.value) return
    if (!taskForm.name.trim()) {
      showToast('error', '请填写任务名称')
      return
    }
    if (taskForm.trigger_type === 'weekly' && !taskForm.weekly_days.length) {
      showToast('error', '请至少选择一个星期')
      return
    }
    if (taskForm.trigger_type === 'cron' && taskForm.target_host_id) {
      showToast('error', 'Cron 首版仅支持主控本机', '请将默认运行电脑设为主控本机后重试')
      return
    }
    if (taskForm.trigger_type === 'cron' && !taskForm.cron_expression.trim()) {
      showToast('error', '请填写 Cron 表达式')
      return
    }
    saving.value = true
    const validSession = operation()
    const editor = editorGeneration
    const current = () => validSession() && editor === editorGeneration
    try {
      const method = editingTask.value ? 'PATCH' : 'POST'
      const path = editingTask.value ? '/tasks/' + editingTask.value.id : '/tasks'
      await api(path, { method, body: JSON.stringify(taskPayload()) })
      if (!current()) return
      taskModalOpen.value = false
      showToast('success', '任务已更新')
      await loadAll({ quiet: true, includeAdmin: false })
    } catch (error) {
      if (!current() || error.name === 'AbortError') return
      if (error.status === 409 && error.message.includes('其他伙伴修改')) {
        showToast('error', '这项任务已被其他人修改', '输入已保留，请核对最新设置后重新编辑')
      } else {
        reportError(error, '保存失败')
      }
    } finally {
      if (current()) saving.value = false
    }
  }

  async function runTask(task) {
    if (!me.value) return
    const current = operation()
    try {
      const requestId = pendingRuns.get(task.id) || manualRequestId()
      pendingRuns.set(task.id, requestId)
      const result = await api('/tasks/' + task.id + '/run', {
        method: 'POST', body: JSON.stringify({ request_id: requestId }),
      })
      if (!current()) return
      pendingRuns.delete(task.id)
      if (result?.run_type === 'remote') {
        showToast('success', '任务已提交到 ' + result.target_host_name,
          result.waiting_reason || '只会在这台电脑执行，不会转到主控本机')
        remoteRunTarget.value = result.remote_run_id
        navigateTo('management', 'hosts')
        return
      }
      const position = result?.queue_position
      showToast(
        'success',
        position ? '任务已加入主控本机队列' : '已提交到主控本机',
        position ? '当前排在第 ' + position + ' 位' : '主控本机会按顺序执行',
      )
      navigateTo('runtime', 'active')
      if (result?.execution_id) openExecution?.({ id: result.execution_id, task_name: task.name })
      await loadAll({ quiet: true, includeAdmin: false })
    } catch (error) {
      if (current()) reportError(error, '无法运行任务')
    }
  }

  async function toggleTask(task) {
    if (!me.value) return
    const current = operation()
    try {
      await api('/tasks/' + task.id, {
        method: 'PATCH',
        body: JSON.stringify({ enabled: !Boolean(task.enabled), version: task.version }),
      })
      if (!current()) return
      showToast('success', task.enabled ? '任务已停用' : '任务已启用')
      await loadAll({ quiet: true, includeAdmin: false })
    } catch (error) {
      if (!current() || error.name === 'AbortError') return
      if (error.status === 409 && error.message.includes('其他伙伴修改')) {
        showToast('error', '状态已被其他人更新', '页面已自动刷新')
      } else reportError(error, '状态更新失败')
      await loadAll({ quiet: true, includeAdmin: false })
    }
  }

  async function confirmDelete() {
    if (!deletingTask.value || !isAdmin.value) return
    const current = operation()
    const target = deletingTask.value
    try {
      await api('/tasks/' + target.id, { method: 'DELETE' })
      if (!current() || deletingTask.value !== target) return
      showToast('success', '任务已彻底删除', '程序、独立环境和运行记录已清理')
      deletingTask.value = null
      await loadAll({ quiet: true, includeAdmin: false })
    } catch (error) {
      if (current() && deletingTask.value === target) reportError(error, '删除失败')
    }
  }

  function toggleWeekday(day) {
    taskForm.weekly_days = taskForm.weekly_days.includes(day)
      ? taskForm.weekly_days.filter((item) => item !== day)
      : [...taskForm.weekly_days, day].sort()
  }

  function resetFilters() {
    Object.assign(filters, { name: '', enabled: 'all', trigger_type: 'all', owner: 'all' })
  }

  return {
    manualTasks,
    attentionTasks,
    readyTasks,
    buildingTasks,
    ownerOptions,
    filteredTasks,
    openCreate,
    openEdit,
    taskPayload,
    saveTask,
    runTask,
    toggleTask,
    confirmDelete,
    toggleWeekday,
    resetFilters,
    loadTargetHosts,
    resetTaskSession,
  }
}
