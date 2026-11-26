<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useWorkspaceContext } from '../../workspace/context'
import { formatTime, taskOwnerKey, triggerDetail, triggerLabel } from '../../lib/format'
import { useWorkflows, workflowStatusLabel } from '../workflows/useWorkflows'
import WorkflowDialog from '../workflows/WorkflowDialog.vue'
import WorkflowEditor from '../workflows/WorkflowEditor.vue'
import WorkflowRunDetail from '../workflows/WorkflowRunDetail.vue'
import WorkflowTriggerHistory from '../schedule/WorkflowTriggerHistory.vue'
import PlanEditor from '../schedule/PlanEditor.vue'
import CreationDialog from './CreationDialog.vue'

const { me, isAdmin, handleSessionExpired, workflowPendingStarts, tasks, tasksLoaded, tasksLoadError,
  taskCenterKind, createObjectOpen, filters, ownerOptions, triggerOptions, envStatusLabel, statusLabel,
  loadAll, resetFilters, openCreate, openAi, openEdit, taskIsActive, viewTaskExecution, runTask, toggleTask,
  deletingTask, rebuildTaskEnvironment, openExecution } = useWorkspaceContext()
const state = useWorkflows({ isAdmin, me, onSessionExpired: handleSessionExpired, workflowPendingStarts, mode: 'definitions' })
const { workflows, definitionsLoaded, loadError, loading, refreshing, busy, editor, selectedRunId,
  actionError, notice, confirmation, pendingStarts, refresh, openRun, closeRun, openEditor, startWorkflow, askConfirmation, confirmAction } = state
const refreshBusy = ref(false)
const filterIsMobile = ref(false)
const filtersExpanded = ref(false)
const hasWorkflows = computed(() => workflows.value.some(flow => !flow.archived))
const kind = computed(() => definitionsLoaded.value && !hasWorkflows.value ? 'task' : taskCenterKind.value)
const activeFilterCount = computed(() => Number(hasWorkflows.value && taskCenterKind.value !== 'all')
  + Number(targetFilter.value !== 'all') + Number(Boolean(filters.name.trim())) + Number(filters.trigger_type !== 'all')
  + (kind.value === 'task' ? Number(filters.enabled !== 'all') + Number(filters.owner !== 'all') : 0))
let filterMedia = null
function updateFilterViewport(event) { filterIsMobile.value = event.matches }
onMounted(() => {
  if (typeof window.matchMedia !== 'function') return
  filterMedia = window.matchMedia('(max-width: 720px)')
  updateFilterViewport(filterMedia)
  if (filterMedia.addEventListener) filterMedia.addEventListener('change', updateFilterViewport)
  else filterMedia.addListener?.(updateFilterViewport)
})
onBeforeUnmount(() => {
  if (filterMedia?.removeEventListener) filterMedia.removeEventListener('change', updateFilterViewport)
  else filterMedia?.removeListener?.(updateFilterViewport)
  filterMedia = null
})
const targetFilter = ref('all')
const expandedRow = ref('')
const targetOptions = computed(() => {
  const options = new Map()
  for (const task of tasks.value) {
    if (task.target_host_id) options.set(String(task.target_host_id), task.target_host_name || '宿主机 #' + task.target_host_id)
  }
  return [...options].sort((a, b) => nameCollator.compare(a[1], b[1]))
})
const planTarget = ref(null)
const triggerTarget = ref(null)
const nameCollator = new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' })
const complete = computed(() => Boolean(tasksLoaded.value && definitionsLoaded.value && !tasksLoadError.value && !loadError.value))
const rows = computed(() => {
  const query = filters.name.trim().toLowerCase()
  const items = []
  if (kind.value !== 'workflow') {
    for (const task of tasks.value) {
      if (targetFilter.value !== 'all' && String(task.target_host_id || 'local') !== targetFilter.value) continue
      if (query && !String(task.name || '').toLowerCase().includes(query)) continue
      if (filters.trigger_type !== 'all' && task.trigger_type !== filters.trigger_type) continue
      if (kind.value === 'task') {
        if (filters.enabled !== 'all' && Boolean(task.enabled) !== (filters.enabled === 'enabled')) continue
        if (filters.owner !== 'all' && taskOwnerKey(task) !== filters.owner) continue
      }
      items.push({ kind: 'task', id: Number(task.id), key: `task:${task.id}`, object: task })
    }
  }
  if (kind.value !== 'task' && ['all', 'local'].includes(targetFilter.value)) {
    for (const flow of workflows.value) {
      if (flow.archived) continue
      if (query && !String(flow.name || '').toLowerCase().includes(query)) continue
      const trigger = flow.schedule_enabled ? flow.trigger_type : 'manual'
      if (filters.trigger_type !== 'all' && trigger !== filters.trigger_type) continue
      items.push({ kind: 'workflow', id: Number(flow.id), key: `workflow:${flow.id}`, object: flow })
    }
  }
  return items.sort((left, right) => nameCollator.compare(String(left.object.name || ''), String(right.object.name || ''))
    || left.kind.localeCompare(right.kind) || left.id - right.id)
})
const countLabel = computed(() => complete.value ? `共 ${rows.value.length} 项` : `已加载 ${rows.value.length} 项`)
function scheduleText({ kind, object }) {
  if (object.trigger_type === 'manual' || (kind === 'workflow' && !object.schedule_enabled)) return '手动'
  if (object.trigger_type === 'cron') return triggerLabel('cron')
  return triggerDetail(object)
}
function nextRunText({ kind, object }) {
  const enabled = kind === 'workflow' ? object.schedule_enabled : object.enabled
  if (!enabled || object.trigger_type === 'manual') return '—'
  if (object.next_run_at) return formatTime(object.next_run_at)
  if (kind === 'task' && object.first_environment_ready === 0 && !object.target_host_id) return '依赖准备中'
  return object.trigger_type === 'interval' ? '本次结束后计算' : '待计算'
}
async function refreshAll() {
  if (refreshBusy.value) return
  refreshBusy.value = true
  try { await Promise.allSettled([loadAll({ quiet: true, includeAdmin: false }), refresh()]) }
  finally { refreshBusy.value = false }
}
function resetCenterFilters() {
  resetFilters()
  targetFilter.value = 'all'
  taskCenterKind.value = 'all'
}
function choosePlan(row) { planTarget.value = { kind: row.kind, id: row.id, created: false } }
function createdTask() {
  if (taskCenterKind.value === 'workflow') taskCenterKind.value = 'task'
  loadAll({ quiet: true, includeAdmin: false })
}
async function execution(id) { closeRun(); await nextTick(); openExecution({ id }) }
function closeConfirmation() { if (!busy.value) confirmation.value = null }
watch(taskCenterKind, () => {
  closeRun()
  if (!busy.value) { confirmation.value = null; state.closeEditor() }
  triggerTarget.value = null
  planTarget.value = null
})
watch(me, (user, previous) => { if (!user || user.id !== previous?.id) { planTarget.value = null; triggerTarget.value = null } }, { flush: 'sync' })
</script>

<template>
  <section class="view-stack task-center-page">
    <section class="panel center-objects">
      <div class="center-filter-region">
      <div v-if="filterIsMobile" class="center-mobile-filter-tools">
        <button class="button secondary compact" type="button" :aria-expanded="filtersExpanded" aria-controls="task-center-filters" @click="filtersExpanded = !filtersExpanded">{{ filtersExpanded ? '收起筛选' : '筛选' }}{{ activeFilterCount ? ' · ' + activeFilterCount : '' }}</button>
        <button class="button ghost compact" type="button" :disabled="refreshBusy || refreshing" @click="refreshAll">{{ refreshBusy || refreshing ? '刷新中…' : '刷新' }}</button>
      </div>
      <div id="task-center-filters" class="filter-bar center-filter-bar" :hidden="filterIsMobile && !filtersExpanded">
        <label v-if="hasWorkflows" class="filter-field"><span>类型</span><select v-model="taskCenterKind"><option value="all">全部</option><option value="task">任务</option><option value="workflow">工作流</option></select></label>
        <label class="filter-field"><span>任务名称</span><input v-model="filters.name" type="search" placeholder="搜索任务名称" /></label>
        <label class="filter-field"><span>触发方式</span><select v-model="filters.trigger_type"><option value="all">全部</option><option v-for="option in triggerOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
        <template v-if="kind === 'task'">
          <label class="filter-field"><span>启用状态</span><select v-model="filters.enabled"><option value="all">全部状态</option><option value="enabled">已启用</option><option value="disabled">已停用</option></select></label>
          <label class="filter-field"><span>归属人</span><select v-model="filters.owner"><option value="all">全部</option><option v-for="owner in ownerOptions" :key="owner.value" :value="owner.value">{{ owner.label }}</option></select></label>
        </template>
        <label class="filter-field"><span>执行电脑</span><select v-model="targetFilter" aria-label="执行电脑"><option value="all">全部</option><option value="local">主控本机</option><option v-for="[id, name] in targetOptions" :key="id" :value="id">{{ name }}</option></select></label>
        <div class="filter-actions"><button v-if="!filterIsMobile" class="button secondary compact" type="button" :disabled="refreshBusy || refreshing" @click="refreshAll">{{ refreshBusy || refreshing ? '刷新中…' : '刷新' }}</button><button class="button ghost compact" type="button" @click="resetCenterFilters">清空筛选</button></div>
      </div>
      </div>
      <header class="panel-heading">
        <div class="center-count" role="status">{{ countLabel }}</div>
        <div class="center-header-actions"><button v-if="isAdmin" class="button secondary compact" type="button" @click="openAi()">AI 创建</button><button v-if="isAdmin" class="button primary compact" type="button" :disabled="Boolean(busy)" @click="openCreate()">新建</button></div>
      </header>
    <div v-if="tasksLoadError || !tasksLoaded" class="center-feedback error" role="alert">{{ tasksLoadError ? '任务加载失败：' + tasksLoadError : '正在加载任务…' }}<span v-if="tasks.length">当前保留已取得的任务。</span><button class="text-button" type="button" :disabled="refreshBusy" @click="refreshAll">重试读取</button></div>
    <div v-if="loadError || !definitionsLoaded" class="center-feedback" :class="loadError ? 'error' : 'loading'" :role="loadError ? 'alert' : 'status'">{{ loadError ? '工作流未完整载入：' + loadError : '正在读取工作流…' }}<span v-if="workflows.length">当前保留已取得的工作流。</span><button v-if="loadError" class="text-button" type="button" :disabled="refreshing" @click="refresh()">重试读取</button></div>
    <div v-if="actionError" class="center-feedback error" role="alert">{{ actionError }}</div>
    <div v-if="notice" class="center-feedback success" role="status">{{ notice }}</div>
      <div v-if="rows.length" class="plan-scroll" tabindex="0" aria-label="任务计划表格">
        <table class="plans-table">
          <thead><tr><th scope="col">任务名称</th><th scope="col">执行程序</th><th scope="col">触发方式</th><th scope="col">启用状态</th><th scope="col">下次执行</th><th scope="col">执行电脑</th><th scope="col">操作</th></tr></thead>
          <tbody>
            <template v-for="row in rows" :key="row.key">
              <tr :data-object-key="row.key">
                <th scope="row"><span class="plan-name" :title="row.object.name">{{ row.object.name }}</span><span v-if="row.kind === 'workflow'" class="center-kind-badge">工作流</span></th>
                <td><span class="plan-program" :title="row.object.project_entry || row.object.script_filename">{{ row.kind === 'workflow' ? (row.object.steps?.length || 0) + ' 个步骤' : row.object.project_entry || row.object.script_filename || '—' }}</span></td>
                <td>{{ scheduleText(row) }}</td>
                <td><button v-if="row.kind === 'task'" class="plan-state" :class="{ enabled: row.object.enabled }" type="button" role="switch" :aria-checked="row.object.enabled" :aria-label="row.object.name + '启用状态'" @click="toggleTask(row.object)"><i></i>{{ row.object.enabled ? '已启用' : '已停用' }}</button>
                  <span v-else>{{ row.object.schedule_enabled ? '定时已启用' : '手动运行' }}</span></td>
                <td class="plan-time">{{ nextRunText(row) }}</td>
                <td>{{ row.object.target_host_name || '主控本机' }}</td>
                <td><div class="center-object-actions">
                  <template v-if="row.kind === 'task'">
                    <button v-if="taskIsActive(row.object)" type="button" @click="viewTaskExecution(row.object)">查看运行</button>
                    <button v-else type="button" :disabled="!row.object.enabled || (row.object.target_host_id ? !isAdmin : row.object.environment_status !== 'ready')" @click="runTask(row.object)">运行</button>
                    <button type="button" :disabled="taskIsActive(row.object)" @click="openEdit(row.object)">编辑</button>
                  </template>
                  <template v-else>
                    <button v-if="row.object.active_run_id" type="button" @click="openRun(row.object.active_run_id)">查看运行</button>
                    <button v-if="isAdmin" type="button" :disabled="Boolean(busy) || (Boolean(row.object.active_run_id) && !pendingStarts.has(row.id))" @click="startWorkflow(row.object)">{{ busy === 'start:' + row.id ? '提交中…' : pendingStarts.has(row.id) ? '重试启动' : '运行' }}</button>
                    <button v-if="isAdmin" type="button" :disabled="Boolean(busy)" @click="openEditor(row.object)">编辑</button>
                  </template>
                  <button type="button" :aria-expanded="expandedRow === row.key" @click="expandedRow = expandedRow === row.key ? '' : row.key">{{ expandedRow === row.key ? '收起' : '更多' }}</button>
                </div></td>
              </tr>
              <tr v-if="expandedRow === row.key" class="plan-expanded"><td colspan="7">
                <div class="plan-details">
                  <p v-if="row.object.description" class="center-description">{{ row.object.description }}</p>
                  <template v-if="row.kind === 'task'">
                    <div class="center-object-facts">
                      <span>{{ row.object.target_host_id ? '运行时准备环境' : envStatusLabel(row.object.environment_status) }}</span>
                      <span>归属人：{{ row.object.created_by_name || '系统迁移' }}</span>
                      <span v-if="row.object.last_status">最近：{{ statusLabel(row.object.last_status) }}</span>
                    </div>
                    <p v-if="row.object.environment_status === 'failed' && row.object.environment_error" class="center-description danger-text">{{ row.object.environment_error }}</p>
                    <div v-if="isAdmin" class="center-more-actions">
                      <button class="text-button" type="button" @click="choosePlan(row)">设置定时</button>
                      <button class="text-button" type="button" @click="openAi(row.object)">AI 助手</button>
                      <button v-if="row.object.environment_status === 'failed'" class="text-button" type="button" @click="rebuildTaskEnvironment(row.object)">修复环境</button>
                      <button class="text-button danger-text" type="button" @click="deletingTask = row.object">删除</button>
                    </div>
                  </template>
                  <template v-else>
                    <ol v-if="row.object.steps?.length" class="center-flow-steps" aria-label="执行顺序"><li v-for="step in row.object.steps" :key="step.position" :class="{ 'danger-text': step.available === false }"><span>{{ step.position }}</span>{{ step.task_name }}{{ step.available === false ? '（不可用）' : '' }}</li></ol>
                    <div class="center-more-actions">
                      <button v-if="row.object.last_run" class="text-button" type="button" @click="openRun(row.object.last_run.id)">最近结果 · {{ workflowStatusLabel(row.object.last_run.status) }}</button>
                      <button v-if="isAdmin" class="text-button" type="button" :disabled="Boolean(busy)" @click="choosePlan(row)">设置定时</button>
                      <button class="text-button" type="button" @click="triggerTarget = row.object">触发记录</button>
                      <button v-if="isAdmin" class="text-button danger-text" type="button" :disabled="Boolean(busy) || Boolean(row.object.active_run_id)" @click="askConfirmation({ type: 'delete', id: row.id, name: row.object.name })">删除</button>
                    </div>
                  </template>
                </div>
              </td></tr>
            </template>
          </tbody>
        </table>
      </div>
      <div v-else class="empty-state compact-empty"><strong>{{ !complete ? '暂无匹配结果' : tasks.length || workflows.length ? '没有匹配的记录' : '还没有任务' }}</strong><p>{{ !complete ? '部分资料仍未取得，请重试读取。' : tasks.length || workflows.length ? '调整筛选条件后再试。' : '点击“新建”，上传 Python 脚本或项目 ZIP。' }}</p></div>
    </section>
    <CreationDialog v-if="createObjectOpen && isAdmin" @created="createdTask" />
    <WorkflowEditor v-if="editor" :state="state" />
    <WorkflowRunDetail v-if="selectedRunId && !confirmation" :state="state" :can-manage="isAdmin" @execution="execution" />
    <WorkflowDialog v-if="confirmation" :title="confirmation.type === 'stop' ? '停止本轮流程' : '删除流程'" :close-disabled="Boolean(busy)" @close="closeConfirmation">
      <p v-if="confirmation.type === 'stop'">停止「{{ confirmation.name }}」本轮运行。后续步骤立即取消，当前步骤完成收尾后结束本轮。</p><p v-else>永久删除「{{ confirmation.name }}」及其流程运行记录？成员任务会保留。</p>
      <div v-if="actionError" class="center-feedback error" role="alert">{{ actionError }}</div>
      <template #footer><button class="button secondary" type="button" :disabled="Boolean(busy)" @click="closeConfirmation">取消</button><button class="button danger" type="button" :disabled="Boolean(busy)" @click="confirmAction">{{ busy ? '正在提交…' : confirmation.type === 'stop' ? '停止本轮' : '删除流程' }}</button></template>
    </WorkflowDialog>
    <PlanEditor v-if="planTarget" :initial-kind="planTarget.kind" :initial-id="planTarget.id" locked-object :created-context="planTarget.created" @close="planTarget = null" @saved="refreshAll" />
    <WorkflowTriggerHistory v-if="triggerTarget" :workflow="triggerTarget" @close="triggerTarget = null" @run="id => { triggerTarget = null; openRun(id) }" />
  </section>
</template>

<style scoped>
.task-center-page { min-width: 0; grid-template-columns: minmax(0, 1fr); }
.task-center-page > .panel { min-width: 0; max-width: 100%; }
.center-filter-region { border-bottom: 1px solid var(--border-soft); }
.center-objects > .center-feedback { margin: 12px 18px; }
.center-mobile-filter-tools { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 12px 14px; }
.center-filter-bar[hidden] { display: none; }
.center-filter-bar { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; }
.center-filter-bar > .filter-field { flex: 1 1 160px; min-width: 0; }
.center-filter-bar input, .center-filter-bar select { font-size: var(--workspace-body, 13px); }
.center-count { color: var(--muted); font-size: var(--workspace-caption, 12px); }
.center-header-actions, .center-object-actions { display: flex; align-items: center; gap: 8px; }
.plan-scroll { max-width: 100%; overflow-x: auto; }
.plans-table { width: 100%; min-width: 870px; border-collapse: collapse; text-align: left; font-size: 13px; }
.plans-table th, .plans-table td { padding: 12px; border-bottom: 1px solid var(--border-soft); }
.plans-table :is(th, td):first-child { padding-left: 18px; }
.plans-table :is(th, td):last-child { padding-right: 18px; }
.plans-table thead th { font-size: 12px; font-weight: 550; background: var(--surface-subtle); color: var(--muted); white-space: nowrap; }
.plans-table tbody th { background: transparent; color: var(--ink); }
.plans-table tbody td { height: 50px; }
.plans-table tbody tr:last-child > * { border-bottom: 0; }
.plans-table tbody tr:hover { background: var(--surface-subtle); }
.plan-name, .plan-program { display: inline-block; max-width: 20em; vertical-align: middle; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.plan-name { font-size: 14px; font-weight: 600; }
.plan-program { max-width: 14em; color: var(--muted); }
.plan-time, .center-object-actions { white-space: nowrap; }
.plan-time { font-variant-numeric: tabular-nums; }
.center-object-actions button { padding: 6px 2px; border: 0; background: transparent; color: var(--brand-strong); font: inherit; cursor: pointer; }
.center-object-actions button:hover { text-decoration: underline; }
.center-object-actions button:disabled { color: var(--faint); cursor: not-allowed; }
.plan-details { display: grid; justify-items: start; gap: 8px; }
.plan-expanded { background: var(--surface-subtle); }
.center-kind-badge { border: 1px solid var(--border); border-radius: 6px; padding: 3px 6px; color: var(--muted); background: var(--surface-subtle); font-size: var(--workspace-caption, 11px); }
.center-description { margin: 0; color: var(--muted); font-size: var(--workspace-body, 12px); line-height: 20px; overflow-wrap: anywhere; }
.center-object-facts { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 16px; color: var(--muted); font-size: var(--workspace-caption, 11px); overflow-wrap: anywhere; }
.center-more-actions { display: flex; flex-wrap: wrap; gap: 14px; }
.center-flow-steps { display: flex; flex-wrap: wrap; gap: 6px 12px; margin: 0; padding: 0; list-style: none; }
.center-flow-steps li { display: inline-flex; gap: 6px; align-items: center; font-size: 12px; overflow-wrap: anywhere; }
.center-flow-steps li + li:before { content: '→'; color: var(--faint); }
.center-flow-steps span { display: grid; place-items: center; width: 20px; height: 20px; flex-shrink: 0; background: var(--surface-subtle); border-radius: 6px; color: var(--muted); font-size: 11px; }
.center-feedback { padding: 12px 14px; border: 1px solid var(--border); border-radius: 8px; color: var(--muted); font-size: 12px; line-height: 20px; overflow-wrap: anywhere; }
.center-feedback.error { border-color: #f0c2b7; background: var(--coral-soft); color: #ad4935; }
.center-feedback.success { border-color: #c9e2d2; background: var(--brand-soft); color: var(--brand-strong); }
@media (max-width: 420px) { .center-filter-bar > .filter-field { flex-basis: 100%; } }
</style>
