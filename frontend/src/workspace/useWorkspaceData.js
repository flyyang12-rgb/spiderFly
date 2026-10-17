import { computed, reactive, ref, watch } from 'vue'
import { baseNavItems, adminNavItems, runtimeTabs, managementTabs } from '../lib/constants'
import { request } from '../lib/api'

export function createWorkspaceDataState() {
  const view = ref('overview')
  const runtimeTab = ref('active')
  const managementTab = ref('services')
  const taskCenterKind = ref('all')
  const createObjectOpen = ref(false)
  const createObjectKind = ref('task')
  const runHistoryKind = ref('task')
  const tasksLoaded = ref(false)
  const tasksLoadError = ref('')
  const remoteRunTarget = ref(null)
  const loading = ref(false)
  const overview = ref({})
  const settings = ref({})
  const saving = ref(false)
  const toast = reactive({ visible: false, type: 'success', title: '', message: '' })
  return { view, runtimeTab, managementTab, remoteRunTarget, loading, overview, settings, saving, toast,
    taskCenterKind, createObjectOpen, createObjectKind, runHistoryKind, tasksLoaded, tasksLoadError }
}

export function useWorkspaceData({
  activeExecutions,
  aiOpen,
  aiSourceThread,
  auditLogs,
  buildingTasks,
  changePasswordOpen,
  completedExecutions,
  deletingTask,
  deletingUser,
  detail,
  editingUser,
  executionFilters,
  executionHistory,
  executionHistoryPage,
  executionHistoryTotal,
  executions,
  isAdmin,
  loadExecutionHistory,
  loading,
  managementTab,
  me,
  overview,
  runtimeTab,
  saving,
  settings,
  stoppingExecution,
  taskModalOpen,
  tasks,
  toast,
  userEditForm,
  users,
  view,
  workflowPendingStarts,
  taskCenterKind,
  createObjectOpen,
  createObjectKind,
  runHistoryKind,
  tasksLoaded,
  tasksLoadError,
  resetCreateSession,
  resetExecutionSession,
  resetTaskSession,
  refreshExecutionDetail,
}) {
  let toastTimer = 0

  let pollTimer = 0
  let sessionGeneration = 0
  let loadGeneration = 0
  let disposed = false
  const controllers = new Set()

  function invalidateRequests() {
    sessionGeneration += 1
    loadGeneration += 1
    for (const controller of controllers) controller.abort()
    controllers.clear()
    loading.value = false
  }

  function currentRequest(owner, session, generation) {
    return !disposed && Boolean(owner) && me.value === owner && session === sessionGeneration && generation === loadGeneration
  }

  const navItems = computed(() => (isAdmin.value ? [...baseNavItems, ...adminNavItems] : baseNavItems))

  const pageTitle = computed(
    () => navItems.value.find((item) => item.id === view.value)?.label || 'SpiderFly',
  )

  const recentExecutions = computed(() => completedExecutions.value.slice(0, 6))

  function showToast(type, title, message = '') {
    Object.assign(toast, { visible: true, type, title, message })
    window.clearTimeout(toastTimer)
    toastTimer = window.setTimeout(() => {
      toast.visible = false
    }, 3600)
  }

  function clearSharedData() {
    invalidateRequests()
    resetCreateSession?.()
    resetExecutionSession?.()
    resetTaskSession?.()
    createObjectOpen.value = false
    createObjectKind.value = 'task'
    taskCenterKind.value = 'all'
    runHistoryKind.value = 'task'
    tasksLoaded.value = false
    tasksLoadError.value = ''
    saving.value = false
    workflowPendingStarts?.clear()
    aiOpen.value = false
    aiSourceThread.value = null
    taskModalOpen.value = false
    deletingTask.value = null
    stoppingExecution.value = null
    editingUser.value = null
    deletingUser.value = null
    userEditForm.password = ''
    userEditForm.confirm_password = ''
    tasks.value = []
    executions.value = []
    executionHistory.value = []
    executionHistoryTotal.value = 0
    executionHistoryPage.value = 1
    Object.assign(executionFilters, {
      task_name: '',
      status: 'all',
      requester: '',
      date_from: '',
      date_to: '',
    })
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
    if (!me.value) {
      loading.value = false
      return
    }
    if (!quiet) loading.value = true
    const owner = me.value
    const session = sessionGeneration
    const generation = ++loadGeneration
    const controller = new AbortController()
    controllers.add(controller)
    const current = () => currentRequest(owner, session, generation)
    try {
      const paths = ['/overview', '/settings', '/tasks', '/executions?limit=100']
      const baseResults = await Promise.allSettled(paths.map(path => request(path, { signal: controller.signal })))
      if (!current()) return
      if (baseResults.some(result => result.status === 'rejected' && result.reason?.status === 401)) {
        handleSessionExpired()
        return
      }
      const targets = [overview, settings, tasks, executions]
      baseResults.forEach((result, index) => {
        if (result.status === 'fulfilled') {
          if ((index === 2 || index === 3) && !Array.isArray(result.value)) {
            if (index === 2) { tasksLoaded.value = false; tasksLoadError.value = '单任务响应不完整，请重试读取' }
            return
          }
          targets[index].value = result.value
          if (index === 2) { tasksLoaded.value = true; tasksLoadError.value = '' }
        } else if (index === 2) {
          tasksLoaded.value = false
          tasksLoadError.value = result.reason?.message || '单任务暂时没有载入，请重试'
        }
      })
      const failed = baseResults.filter(result => result.status === 'rejected')
      if (failed.length && !quiet) showToast('error', '部分数据暂时没有载入', failed.map(result => result.reason?.message || '连接失败').join('；'))
      if (view.value === 'runtime' && runtimeTab.value === 'executions' && runHistoryKind.value === 'task') {
        await loadExecutionHistory({ quiet: true })
      }
      if (!current()) return
      if (detail.value) await refreshExecutionDetail?.({ quiet: true })
      if (!current()) return
      if (includeAdmin && isAdmin.value) {
        const adminResults = await Promise.allSettled([request('/users', { signal: controller.signal }), request('/audit-logs?limit=100', { signal: controller.signal })])
        if (!current()) return
        if (adminResults.some(result => result.status === 'rejected' && result.reason?.status === 401)) { handleSessionExpired(); return }
        if (adminResults[0].status === 'fulfilled') users.value = adminResults[0].value
        if (adminResults[1].status === 'fulfilled') auditLogs.value = adminResults[1].value
      }
    } catch (error) {
      if (!current() || error.name === 'AbortError') return
      if (error.status === 401) handleSessionExpired()
      else if (!quiet) showToast('error', '数据暂时没有载入', error.message)
    } finally {
      controllers.delete(controller)
      if (current()) { loading.value = false; schedulePoll() }
    }
  }

  function schedulePoll() {
    window.clearTimeout(pollTimer)
    if (!me.value || disposed) return
    const delay = activeExecutions.value.length || buildingTasks.value.length ? 1200 : 5000
    pollTimer = window.setTimeout(async () => {
      await loadAll({ quiet: true, includeAdmin: false })
    }, delay)
  }

  function navigateTo(target, tab = null) {
    if (target === 'management' && ['ai', 'settings', 'users'].includes(tab)) tab = 'services'
    if (target === 'workflows') { taskCenterKind.value = 'workflow'; target = 'tasks' }
    if (target === 'management' && tab === 'apps') { openCreate('task', { preserveDraft: true }); return }
    if (target === 'management' && !isAdmin.value) {
      view.value = 'overview'
      showToast('error', '此区域仅管理员可用', '普通成员可使用工作台、任务中心和运行中心')
      return
    }
    if (target === 'runtime') runtimeTab.value = runtimeTabs.some((item) => item.id === tab) ? tab : 'active'
    if (target === 'management' && managementTabs.some((item) => item.id === tab)) managementTab.value = tab
    view.value = target
  }

  function openCreate(kind = 'task', { preserveDraft = false } = {}) {
    if (!isAdmin.value) { showToast('error', '请联系管理员新建任务或工作流'); return }
    if (!preserveDraft) resetCreateSession?.()
    view.value = 'tasks'
    createObjectKind.value = kind === 'workflow' ? 'workflow' : 'task'
    createObjectOpen.value = true
  }

  function handleKeydown(event) {
    if (event.key !== 'Escape') return
    if (event.defaultPrevented || event.target?.closest?.('.workflow-dialog')) return
    if (saving.value) return
    taskModalOpen.value = false
    detail.value = null
    deletingTask.value = null
    changePasswordOpen.value = false
    toast.visible = false
  }

  function stopPolling() {
    window.clearTimeout(pollTimer)
  }

  function dispose() {
    disposed = true
    invalidateRequests()
    stopPolling()
    window.clearTimeout(toastTimer)
  }

  watch(me, (owner, previous) => { if (owner !== previous) clearSharedData() }, { flush: 'sync' })

  return {
    navItems,
    pageTitle,
    recentExecutions,
    showToast,
    clearSharedData,
    handleSessionExpired,
    loadAll,
    schedulePoll,
    navigateTo,
    openCreate,
    handleKeydown,
    stopPolling,
    dispose,
  }
}
