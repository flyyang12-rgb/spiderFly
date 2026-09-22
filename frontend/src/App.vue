<script setup>
import { useWorkspace } from './workspace/useWorkspace'
import AiAssistant from './AiAssistant.vue'
import MaintenanceNotices from './MaintenanceNotices.vue'
import LoginPage from './features/auth/LoginPage.vue'
import ActiveQueue from './features/executions/ActiveQueue.vue'
import OverviewPage from './pages/OverviewPage.vue'
import TasksPage from './features/tasks/TasksPage.vue'
import SchedulePage from './features/schedule/SchedulePage.vue'
import ExecutionHistory from './features/executions/ExecutionHistory.vue'
import CreateTaskPage from './features/apps/CreateTaskPage.vue'
import ModelSettingsPage from './features/settings/ModelSettingsPage.vue'
import SystemSettingsPage from './features/settings/SystemSettingsPage.vue'
import MembersPage from './features/members/MembersPage.vue'
import AuditPage from './features/members/AuditPage.vue'
import HostsPage from './features/hosts/HostsPage.vue'
import TaskEditor from './features/tasks/TaskEditor.vue'
import ExecutionDetail from './features/executions/ExecutionDetail.vue'
import StopExecutionDialog from './features/executions/StopExecutionDialog.vue'
import DeleteTaskDialog from './features/tasks/DeleteTaskDialog.vue'
import MemberEditor from './features/members/MemberEditor.vue'
import DeleteMemberDialog from './features/members/DeleteMemberDialog.vue'
import PasswordDialog from './features/auth/PasswordDialog.vue'
import ToastNotice from './components/ToastNotice.vue'

const {
  activeExecutions,
  aiOpen,
  aiTaskId,
  authChecking,
  canChangePassword,
  changePasswordOpen,
  deletingTask,
  deletingUser,
  detail,
  editingUser,
  executions,
  isAdmin,
  isSuperAdmin,
  loading,
  logout,
  managementTab,
  managementTabs,
  me,
  navItems,
  navigateTo,
  openAi,
  openCreate,
  openExecution,
  overview,
  pageTitle,
  prepareAiDraft,
  queuedExecutions,
  remoteRunTarget,
  roleLabel,
  runningExecution,
  runtimeTab,
  runtimeTabs,
  settings,
  showToast,
  stoppingExecution,
  taskModalOpen,
  tasks,
  toast,
  users,
  view,
} = useWorkspace()

const initialRemoteRun = Number(new URLSearchParams(window.location.search).get('remote_run'))
if (Number.isInteger(initialRemoteRun) && initialRemoteRun > 0) {
  remoteRunTarget.value = initialRemoteRun
  view.value = 'management'
  managementTab.value = 'hosts'
}

function openRemoteRun(id) {
  remoteRunTarget.value = id
  view.value = 'management'
  managementTab.value = 'hosts'
}

function clearRemoteRunTarget() {
  remoteRunTarget.value = null
  const url = new URL(window.location.href)
  url.searchParams.delete('remote_run')
  window.history.replaceState({}, '', url)
}
</script>

<template>
  <div v-if="authChecking" class="auth-screen">
    <div class="auth-loading" role="status">
      <div class="brand-mark large" aria-hidden="true"><span></span><span></span><span></span></div>
      <span class="spinner"></span>
      <strong>正在连接共享任务中心…</strong>
    </div>
  </div>

  <LoginPage v-else-if="!me" />

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
          <small>{{
            queuedExecutions.length ? queuedExecutions.length + ' 项正在排队' : '队列目前为空'
          }}</small>
        </div>
      </div>

      <div class="account-card">
        <span class="avatar">{{ (me.display_name || me.username).slice(0, 1) }}</span>
        <div>
          <strong>{{ me.display_name || me.username }}</strong
          ><small>{{ roleLabel(me.role) }}</small>
        </div>
        <span class="account-actions">
          <button
            v-if="canChangePassword"
            type="button"
            aria-label="修改密码"
            title="修改密码"
            @click="changePasswordOpen = true"
          >
            ✎
          </button>
          <button type="button" aria-label="退出登录" title="退出登录" @click="logout">↪</button>
        </span>
      </div>
      <div class="sidebar-footer">SPIDERFLY · PY CONTROL</div>
    </aside>

    <main class="workspace">
      <header class="workspace-header">
        <div>
          <h1>{{ pageTitle }}</h1>
        </div>
        <div class="header-actions">
          <MaintenanceNotices
            :key="me.id"
            :can-manage="isAdmin"
            @open-task="
              (id) => {
                aiTaskId = id
                aiOpen = true
              }
            "
            @open-execution="(id) => openExecution({ id })"
            @open-remote-run="openRemoteRun"
            @notify="(title, message) => showToast('info', title, message)"
          />
          <button v-if="view === 'tasks' && isAdmin" class="button ghost" type="button" @click="openAi()">
            AI 创建
          </button>
          <span class="live-state"
            ><i class="status-dot" :class="runningExecution ? 'running' : 'success'"></i
            >{{ runningExecution ? '正在运行 1 项' : '执行器空闲' }}</span
          >
          <button v-if="view === 'tasks'" class="button primary" type="button" @click="openCreate">
            <span aria-hidden="true">＋</span> 新建任务
          </button>
        </div>
      </header>

      <nav v-if="view === 'runtime'" class="center-tabs" aria-label="运行中心功能">
        <button
          v-for="tab in runtimeTabs"
          :key="tab.id"
          type="button"
          :class="{ active: runtimeTab === tab.id }"
          @click="runtimeTab = tab.id"
        >
          {{ tab.label }}
          <i v-if="tab.id === 'active' && activeExecutions.length">{{ activeExecutions.length }}</i>
        </button>
      </nav>
      <nav v-else-if="view === 'management' && isAdmin" class="center-tabs" aria-label="管理中心功能">
        <button
          v-for="tab in managementTabs"
          :key="tab.id"
          type="button"
          :class="{ active: managementTab === tab.id }"
          @click="managementTab = tab.id"
        >
          {{ tab.label }}
        </button>
      </nav>

      <div v-if="loading" class="loading-panel" role="status">
        <span class="spinner"></span>正在载入共享任务…
      </div>

      <template v-else>
        <ActiveQueue v-if="view === 'runtime' && runtimeTab === 'active'" />
        <OverviewPage v-if="view === 'overview'" />

        <TasksPage v-else-if="view === 'tasks'" />

        <SchedulePage v-else-if="view === 'runtime' && runtimeTab === 'queue'" />

        <ExecutionHistory v-else-if="view === 'runtime' && runtimeTab === 'executions'" />

        <CreateTaskPage v-else-if="view === 'management' && managementTab === 'apps' && isAdmin" />

        <ModelSettingsPage v-else-if="view === 'management' && managementTab === 'ai' && isAdmin" />

        <SystemSettingsPage v-else-if="view === 'management' && managementTab === 'settings' && isAdmin" />

        <MembersPage v-else-if="view === 'management' && managementTab === 'users' && isAdmin" />

        <AuditPage v-else-if="view === 'management' && managementTab === 'audit' && isAdmin" />

        <HostsPage v-else-if="view === 'management' && managementTab === 'hosts' && isAdmin" :open-run-id="remoteRunTarget" @run-opened="clearRemoteRunTarget" />
      </template>
    </main>

    <AiAssistant
      v-if="aiOpen && isAdmin"
      :task-id="aiTaskId"
      @close="aiOpen = false"
      @use-draft="prepareAiDraft"
      @open-execution="
        (id) => {
          aiOpen = false
          openExecution({ id })
        }
      "
    />

    <TaskEditor v-if="taskModalOpen" />

    <ExecutionDetail v-if="detail" />

    <StopExecutionDialog v-if="stoppingExecution && isAdmin" />

    <DeleteTaskDialog v-if="deletingTask && isAdmin" />

    <MemberEditor v-if="editingUser && isSuperAdmin" />

    <DeleteMemberDialog v-if="deletingUser && isSuperAdmin" />

    <PasswordDialog v-if="changePasswordOpen && canChangePassword" />
  </div>

  <ToastNotice v-if="toast.visible" />
</template>
