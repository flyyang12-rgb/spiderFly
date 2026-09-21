import { onBeforeUnmount, onMounted, provide, watch } from 'vue'
import { workspaceKey } from './context'
import * as format from '../lib/format'
import * as constants from '../lib/constants'
import { createAuthState, useAuth } from '../features/auth/useAuth'
import { createExecutionsState, useExecutions } from '../features/executions/useExecutions'
import { createTasksState, useTasks } from '../features/tasks/useTasks'
import { createScheduleState, useSchedule } from '../features/schedule/useSchedule'
import { createMembersState, useMembers } from '../features/members/useMembers'
import { createAppsState, useApps } from '../features/apps/useApps'
import { createWorkspaceDataState, useWorkspaceData } from './useWorkspaceData'

export function useWorkspace() {
  const authState = createAuthState()
  const executionsState = createExecutionsState()
  const tasksState = createTasksState()
  const scheduleState = createScheduleState()
  const membersState = createMembersState()
  const appsState = createAppsState()
  const workspaceState = createWorkspaceDataState()

  const auth = useAuth({
    authChecking: authState.authChecking,
    changePasswordOpen: authState.changePasswordOpen,
    clearSharedData: (...args) => workspace.clearSharedData(...args),
    loadAll: (...args) => workspace.loadAll(...args),
    loginBusy: authState.loginBusy,
    loginForm: authState.loginForm,
    me: authState.me,
    passwordForm: authState.passwordForm,
    saving: workspaceState.saving,
    showToast: (...args) => workspace.showToast(...args),
    stopPolling: () => workspace.stopPolling(),
    view: workspaceState.view,
  })

  const executions = useExecutions({
    EXECUTION_PAGE_SIZE: executionsState.EXECUTION_PAGE_SIZE,
    artifactDownload: executionsState.artifactDownload,
    detail: executionsState.detail,
    executionFilters: executionsState.executionFilters,
    executionHistory: executionsState.executionHistory,
    executionHistoryLoading: executionsState.executionHistoryLoading,
    executionHistoryPage: executionsState.executionHistoryPage,
    executionHistoryTotal: executionsState.executionHistoryTotal,
    executions: executionsState.executions,
    handleSessionExpired: (...args) => workspace.handleSessionExpired(...args),
    isAdmin: auth.isAdmin,
    loadAll: (...args) => workspace.loadAll(...args),
    me: authState.me,
    navigateTo: (...args) => workspace.navigateTo(...args),
    showToast: (...args) => workspace.showToast(...args),
    stoppingBusy: executionsState.stoppingBusy,
    stoppingExecution: executionsState.stoppingExecution,
  })

  const tasks = useTasks({
    deletingTask: tasksState.deletingTask,
    detail: executionsState.detail,
    editingTask: tasksState.editingTask,
    executions: executionsState.executions,
    filters: tasksState.filters,
    isAdmin: auth.isAdmin,
    loadAll: (...args) => workspace.loadAll(...args),
    me: authState.me,
    navigateTo: (...args) => workspace.navigateTo(...args),
    saving: workspaceState.saving,
    showToast: (...args) => workspace.showToast(...args),
    taskForm: tasksState.taskForm,
    taskIsActive: (...args) => executions.taskIsActive(...args),
    taskModalOpen: tasksState.taskModalOpen,
    tasks: tasksState.tasks,
    targetHosts: tasksState.targetHosts,
    targetHostsLoading: tasksState.targetHostsLoading,
  })

  const schedule = useSchedule({
    executions: executionsState.executions,
    scheduleScope: scheduleState.scheduleScope,
    tasks: tasksState.tasks,
  })

  const members = useMembers({
    auditLogs: membersState.auditLogs,
    creatingUser: membersState.creatingUser,
    deletingUser: membersState.deletingUser,
    deletingUserBusy: membersState.deletingUserBusy,
    editingUser: membersState.editingUser,
    isAdmin: auth.isAdmin,
    isSuperAdmin: auth.isSuperAdmin,
    logout: (...args) => auth.logout(...args),
    me: authState.me,
    savingUser: membersState.savingUser,
    showToast: (...args) => workspace.showToast(...args),
    userEditForm: membersState.userEditForm,
    userForm: membersState.userForm,
    users: membersState.users,
  })

  const apps = useApps({
    aiOpen: appsState.aiOpen,
    aiSourceThread: appsState.aiSourceThread,
    aiTaskId: appsState.aiTaskId,
    appForm: appsState.appForm,
    isAdmin: auth.isAdmin,
    loadAll: (...args) => workspace.loadAll(...args),
    navigateTo: (...args) => workspace.navigateTo(...args),
    showToast: (...args) => workspace.showToast(...args),
    uploadKey: appsState.uploadKey,
    uploading: appsState.uploading,
  })

  const workspace = useWorkspaceData({
    activeExecutions: executions.activeExecutions,
    aiOpen: appsState.aiOpen,
    aiSourceThread: appsState.aiSourceThread,
    auditLogs: membersState.auditLogs,
    buildingTasks: tasks.buildingTasks,
    changePasswordOpen: authState.changePasswordOpen,
    completedExecutions: executions.completedExecutions,
    deletingTask: tasksState.deletingTask,
    deletingUser: membersState.deletingUser,
    detail: executionsState.detail,
    editingUser: membersState.editingUser,
    executionFilters: executionsState.executionFilters,
    executionHistory: executionsState.executionHistory,
    executionHistoryPage: executionsState.executionHistoryPage,
    executionHistoryTotal: executionsState.executionHistoryTotal,
    executions: executionsState.executions,
    isAdmin: auth.isAdmin,
    loadExecutionHistory: (...args) => executions.loadExecutionHistory(...args),
    loading: workspaceState.loading,
    managementTab: workspaceState.managementTab,
    me: authState.me,
    overview: workspaceState.overview,
    runtimeTab: workspaceState.runtimeTab,
    settings: workspaceState.settings,
    stoppingExecution: executionsState.stoppingExecution,
    taskModalOpen: tasksState.taskModalOpen,
    tasks: tasksState.tasks,
    toast: workspaceState.toast,
    userEditForm: membersState.userEditForm,
    users: membersState.users,
    view: workspaceState.view,
  })

  watch(() => executionsState.detail.value?.id, executions.resetArtifactDownload, { flush: 'sync' })
  watch([workspaceState.view, workspaceState.runtimeTab], ([view, tab]) => {
    if (view === 'runtime' && tab === 'executions') {
      executions.loadExecutionHistory({ quiet: executionsState.executionHistory.value.length > 0 })
    }
  })
  onMounted(() => {
    document.addEventListener('keydown', workspace.handleKeydown)
    auth.checkAuth()
  })
  onBeforeUnmount(() => {
    document.removeEventListener('keydown', workspace.handleKeydown)
    workspace.dispose()
    executions.resetArtifactDownload()
  })
  const context = {
    ...constants,
    ...format,
    ...authState,
    ...auth,
    ...executionsState,
    ...executions,
    ...tasksState,
    ...tasks,
    ...scheduleState,
    ...schedule,
    ...membersState,
    ...members,
    ...appsState,
    ...apps,
    ...workspaceState,
    ...workspace,
  }
  provide(workspaceKey, context)
  return context
}
