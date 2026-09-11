import { computed, reactive, ref } from 'vue'
import { baseNavItems, adminNavItems, runtimeTabs, managementTabs } from '../lib/constants'
import { request } from '../lib/api'

export function createWorkspaceDataState() {
  const view = ref('overview')
  const runtimeTab = ref('active')
  const managementTab = ref('apps')
  const loading = ref(false)
  const overview = ref({})
  const settings = ref({})
  const saving = ref(false)
  const toast = reactive({ visible: false, type: 'success', title: '', message: '' })
  return { view, runtimeTab, managementTab, loading, overview, settings, saving, toast }
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
  settings,
  stoppingExecution,
  taskModalOpen,
  tasks,
  toast,
  userEditForm,
  users,
  view,
}) {
  let toastTimer = 0

  let pollTimer = 0

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
    aiOpen.value = false
    aiSourceThread.value = null
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
      if (view.value === 'runtime' && runtimeTab.value === 'executions') {
        await loadExecutionHistory({ quiet: true })
      }
      if (detail.value) {
        const detailId = detail.value.id
        const refreshed = await request('/executions/' + detailId)
        if (detail.value?.id === detailId) detail.value = refreshed
      }
      if (includeAdmin && isAdmin.value) {
        const adminResults = await Promise.all([request('/users'), request('/audit-logs?limit=100')])
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
    if (!me.value) return
    const delay = activeExecutions.value.length || buildingTasks.value.length ? 1200 : 5000
    pollTimer = window.setTimeout(async () => {
      await loadAll({ quiet: true, includeAdmin: false })
    }, delay)
  }

  function navigateTo(target, tab = null) {
    if (target === 'management' && !isAdmin.value) {
      view.value = 'overview'
      showToast('error', '此区域仅管理员可用', '普通成员可使用工作台、任务中心和运行中心')
      return
    }
    if (target === 'runtime') runtimeTab.value = runtimeTabs.some((item) => item.id === tab) ? tab : 'active'
    if (target === 'management' && managementTabs.some((item) => item.id === tab)) managementTab.value = tab
    view.value = target
  }

  function handleKeydown(event) {
    if (event.key !== 'Escape') return
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
    stopPolling()
    window.clearTimeout(toastTimer)
  }

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
    handleKeydown,
    stopPolling,
    dispose,
  }
}
