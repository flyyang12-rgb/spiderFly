import { computed, reactive, ref } from 'vue'
import { triggerConfig, taskOwnerKey } from '../../lib/format'
import { triggerOptions } from '../../lib/constants'
import { request } from '../../lib/api'

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
  me,
  navigateTo,
  saving,
  showToast,
  taskForm,
  taskIsActive,
  taskModalOpen,
  tasks,
  targetHosts,
  targetHostsLoading,
}) {
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
      failure_screenshot: Boolean(task.failure_screenshot),
      trigger_type: triggerOptions.some((item) => item.value === task.trigger_type)
        ? task.trigger_type
        : 'manual',
      target_host_id: task.target_host_id ? String(task.target_host_id) : '',
      daily_time: config.time || '09:00',
      weekly_days: config.weekdays || [1],
      weekly_time: config.time || '09:00',
    })
    taskModalOpen.value = true
    loadTargetHosts()
  }

  async function loadTargetHosts() {
    if (!isAdmin.value || targetHostsLoading.value) return
    targetHostsLoading.value = true
    try {
      targetHosts.value = await request('/hosts')
    } catch (error) {
      showToast('error', '宿主机列表载入失败', error.message)
    } finally {
      targetHostsLoading.value = false
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
      showToast(
        'success',
        position ? '任务已加入队列' : '运行请求已提交',
        position ? '当前排在第 ' + position + ' 位' : '共享电脑会按顺序执行',
      )
      navigateTo('runtime', 'active')
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
      } else showToast('error', '状态更新失败', error.message)
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
  }
}
