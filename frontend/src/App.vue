<script setup>
import { useWorkspace } from './workspace/useWorkspace'
import AiAssistant from './AiAssistant.vue'
import MaintenanceNotices from './MaintenanceNotices.vue'
import LoginPage from './features/auth/LoginPage.vue'
import ActiveQueue from './features/executions/ActiveQueue.vue'
import OverviewPage from './pages/OverviewPage.vue'
import TaskCenterPage from './features/taskCenter/TaskCenterPage.vue'
import SchedulePage from './features/schedule/SchedulePage.vue'
import RunHistoryPage from './features/executions/RunHistoryPage.vue'
import ServiceSettingsPage from './features/settings/ServiceSettingsPage.vue'
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
  handleSessionExpired,
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
const navIcons = {
  overview: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  tasks: 'M8 5h13 M8 12h13 M8 19h13 M3 5h.01 M3 12h.01 M3 19h.01',
  runtime: 'M9 5l10 7-10 7z M4 5v14',
  management: 'M4 7h16 M4 17h16 M8 4v6 M16 14v6',
}
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

function closeAccountMenu(event) {
  const menu = event.currentTarget.closest('details')
  if (menu) { menu.open = false; menu.querySelector('summary')?.focus() }
}
</script>

<template>
  <div v-if="authChecking" class="auth-screen">
    <div class="auth-loading" role="status">
      <div class="brand-mark large" aria-hidden="true"><span></span><span></span><span></span></div>
      <span class="spinner"></span>
      <strong>正在连接…</strong>
    </div>
  </div>

  <LoginPage v-else-if="!me" />

  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand-block">
        <div class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></div>
        <div><strong>SpiderFly</strong><small>自动化控制台</small></div>
      </div>

      <nav class="sidebar-nav" aria-label="主导航">
        <button
          v-for="item in navItems"
          :key="item.id"
          type="button"
          :aria-label="item.label"
          :title="item.label"
          :class="{ active: view === item.id }"
          :aria-current="view === item.id ? 'page' : undefined"
          @click="navigateTo(item.id)"
        >
          <svg class="nav-mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="navIcons[item.id]" /></svg>
          <span>{{ item.label }}</span>
          <i v-if="item.id === 'runtime' && activeExecutions.length">{{ activeExecutions.length }}</i>
        </button>
      </nav>

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
    </aside>

    <main class="workspace" :class="{ 'hosts-workspace': view === 'management' && managementTab === 'hosts' }">
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
          <span v-if="view === 'overview' || view === 'runtime'" class="live-state"
            ><i class="status-dot" :class="runningExecution ? 'running' : 'success'"></i
            >{{ runningExecution ? '主控运行中' : '主控空闲' }}</span
          >
          <details class="mobile-account" @keydown.esc.prevent.stop="closeAccountMenu">
            <summary aria-label="我的账号"><span class="avatar">{{ (me.display_name || me.username).slice(0, 1) }}</span></summary>
            <div class="account-popover">
              <strong>{{ me.display_name || me.username }}</strong><small>{{ roleLabel(me.role) }}</small>
              <button v-if="canChangePassword" type="button" @click="closeAccountMenu($event); changePasswordOpen = true">修改密码</button>
              <button type="button" @click="closeAccountMenu($event); logout()">退出登录</button>
            </div>
          </details>
        </div>
      </header>

      <nav v-if="view === 'runtime'" class="center-tabs" aria-label="运行中心功能">
        <button
          v-for="tab in runtimeTabs"
          :key="tab.id"
          type="button"
          :class="{ active: runtimeTab === tab.id }"
          :aria-current="runtimeTab === tab.id ? 'page' : undefined"
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
          :aria-current="managementTab === tab.id ? 'page' : undefined"
          @click="managementTab = tab.id"
        >
          {{ tab.label }}
        </button>
      </nav>

      <div v-if="loading" class="loading-panel" role="status">
        <span class="spinner"></span>正在载入…
      </div>

      <div v-else class="workspace-body">
        <ActiveQueue v-if="view === 'runtime' && runtimeTab === 'active'" />
        <OverviewPage v-if="view === 'overview'" />

        <TaskCenterPage v-else-if="view === 'tasks'" />

        <SchedulePage v-else-if="view === 'runtime' && runtimeTab === 'queue'" />

        <RunHistoryPage v-else-if="view === 'runtime' && runtimeTab === 'executions'" />

        <ServiceSettingsPage v-else-if="view === 'management' && managementTab === 'services' && isAdmin" :key="me.id" @session-expired="handleSessionExpired" />

        <AuditPage v-else-if="view === 'management' && managementTab === 'audit' && isAdmin" />

        <HostsPage v-else-if="view === 'management' && managementTab === 'hosts' && isAdmin" :open-run-id="remoteRunTarget" @run-opened="clearRemoteRunTarget" />
      </div>
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
