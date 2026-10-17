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
const { workflows, definitionsLoaded, loadError, loading, refreshing, showArchived, busy, editor, selectedRunId,
  actionError, notice, confirmation, pendingStarts, refresh, openRun, closeRun, openEditor, startWorkflow, askConfirmation, confirmAction } = state
const refreshBusy = ref(false)
const filterIsMobile = ref(false)
const filtersExpanded = ref(false)
const activeFilterCount = computed(() => Number(taskCenterKind.value !== 'all')
  + Number(taskCenterKind.value !== 'task' && showArchived.value)
  + Number(Boolean(filters.name.trim())) + Number(filters.trigger_type !== 'all')
  + (taskCenterKind.value === 'task' ? Number(filters.enabled !== 'all') + Number(filters.owner !== 'all') : 0))
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
const planTarget = ref(null)
const triggerTarget = ref(null)
const nameCollator = new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' })
const complete = computed(() => Boolean(tasksLoaded.value && definitionsLoaded.value && !tasksLoadError.value && !loadError.value))
const rows = computed(() => {
  const query = filters.name.trim().toLowerCase()
  const items = []
  if (taskCenterKind.value !== 'workflow') {
    for (const task of tasks.value) {
      if (query && !String(task.name || '').toLowerCase().includes(query)) continue
      if (filters.trigger_type !== 'all' && task.trigger_type !== filters.trigger_type) continue
      if (taskCenterKind.value === 'task') {
        if (filters.enabled !== 'all' && Boolean(task.enabled) !== (filters.enabled === 'enabled')) continue
        if (filters.owner !== 'all' && taskOwnerKey(task) !== filters.owner) continue
      }
      items.push({ kind: 'task', id: Number(task.id), key: `task:${task.id}`, object: task })
    }
  }
  if (taskCenterKind.value !== 'task') {
    for (const flow of workflows.value) {
      if (!showArchived.value && flow.archived) continue
      if (query && !String(flow.name || '').toLowerCase().includes(query)) continue
      const trigger = flow.schedule_enabled ? flow.trigger_type : 'manual'
      if (filters.trigger_type !== 'all' && trigger !== filters.trigger_type) continue
      items.push({ kind: 'workflow', id: Number(flow.id), key: `workflow:${flow.id}`, object: flow })
    }
  }
  return items.sort((left, right) => nameCollator.compare(String(left.object.name || ''), String(right.object.name || ''))
    || left.kind.localeCompare(right.kind) || left.id - right.id)
})
const countLabel = computed(() => complete.value ? `${rows.value.length} 个对象` : `已显示 ${rows.value.length} 个对象 · 载入不完整`)
async function refreshAll() {
  if (refreshBusy.value) return
  refreshBusy.value = true
  try { await Promise.allSettled([loadAll({ quiet: true, includeAdmin: false }), refresh()]) }
  finally { refreshBusy.value = false }
}
function resetCenterFilters() { resetFilters(); showArchived.value = false }
function choosePlan(row) { planTarget.value = { kind: row.kind, id: row.id, created: false } }
function createdObject(target) { if (target.kind === 'workflow') refresh(true); else loadAll({ quiet: true, includeAdmin: false }) }
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
      <header class="panel-heading">
        <div class="center-count" role="status">{{ countLabel }}</div>
        <div class="center-header-actions"><button v-if="isAdmin" class="button secondary compact" type="button" @click="openAi()">AI 创建</button><button v-if="isAdmin" class="button primary compact" type="button" :disabled="Boolean(busy)" @click="openCreate(taskCenterKind === 'workflow' ? 'workflow' : 'task')">新建</button></div>
      </header>
      <div class="center-filter-region">
      <div v-if="filterIsMobile" class="center-mobile-filter-tools">
        <button class="button secondary compact" type="button" :aria-expanded="filtersExpanded" aria-controls="task-center-filters" @click="filtersExpanded = !filtersExpanded">{{ filtersExpanded ? '收起筛选' : '筛选' }}{{ activeFilterCount ? ' · ' + activeFilterCount : '' }}</button>
        <button class="button ghost compact" type="button" :disabled="refreshBusy || refreshing" @click="refreshAll">{{ refreshBusy || refreshing ? '刷新中…' : '刷新' }}</button>
      </div>
      <div id="task-center-filters" class="filter-bar center-filter-bar" :hidden="filterIsMobile && !filtersExpanded">
        <label class="filter-field"><span>类型</span><select v-model="taskCenterKind"><option value="all">全部</option><option value="task">单任务</option><option value="workflow">工作流</option></select></label>
        <label class="filter-field"><span>名称</span><input v-model="filters.name" type="search" placeholder="搜索任务或工作流" /></label>
        <label class="filter-field"><span>触发方式</span><select v-model="filters.trigger_type"><option value="all">全部</option><option v-for="option in triggerOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
        <template v-if="taskCenterKind === 'task'">
          <label class="filter-field"><span>启用状态</span><select v-model="filters.enabled"><option value="all">全部状态</option><option value="enabled">已启用</option><option value="disabled">已停用</option></select></label>
          <label class="filter-field"><span>归属人</span><select v-model="filters.owner"><option value="all">全部</option><option v-for="owner in ownerOptions" :key="owner.value" :value="owner.value">{{ owner.label }}</option></select></label>
        </template>
        <label v-if="taskCenterKind !== 'task'" class="center-archive-filter"><input v-model="showArchived" type="checkbox" />显示已归档流程</label>
        <div class="filter-actions"><button v-if="!filterIsMobile" class="button secondary compact" type="button" :disabled="refreshBusy || refreshing" @click="refreshAll">{{ refreshBusy || refreshing ? '刷新中…' : '刷新' }}</button><button class="button ghost compact" type="button" @click="resetCenterFilters">清空筛选</button></div>
      </div>
      </div>
    <div v-if="tasksLoadError || !tasksLoaded" class="center-feedback error" role="alert">{{ tasksLoadError ? '单任务未完整载入：' + tasksLoadError : '单任务尚未载入完成。' }}<span v-if="tasks.length">当前保留已取得的任务。</span><button class="text-button" type="button" :disabled="refreshBusy" @click="refreshAll">重试读取</button></div>
    <div v-if="loadError || !definitionsLoaded" class="center-feedback" :class="loadError ? 'error' : 'loading'" :role="loadError ? 'alert' : 'status'">{{ loadError ? '工作流未完整载入：' + loadError : '正在读取工作流…' }}<span v-if="workflows.length">当前保留已取得的工作流。</span><button v-if="loadError" class="text-button" type="button" :disabled="refreshing" @click="refresh()">重试读取</button></div>
    <div v-if="actionError" class="center-feedback error" role="alert">{{ actionError }}</div>
    <div v-if="notice" class="center-feedback success" role="status">{{ notice }}</div>
      <ul v-if="rows.length" class="center-object-list" aria-label="任务与工作流列表">
        <li v-for="row in rows" :key="row.key" :data-object-key="row.key">
          <div class="center-object-main">
            <div class="center-object-name"><strong>{{ row.object.name }}</strong><span class="center-kind-badge">{{ row.kind === 'task' ? '单任务' : '工作流' }}</span><span v-if="row.kind === 'workflow' && row.object.archived" class="center-kind-badge">已归档</span></div>
            <p v-if="row.object.description" class="center-description">{{ row.object.description }}</p>
            <div class="center-object-facts">
              <span>运行电脑：{{ row.kind === 'workflow' ? '主控本机' : row.object.target_host_name || '主控本机' }}</span>
              <span>{{ row.kind === 'workflow' && !row.object.schedule_enabled ? '手动 · 定时关闭' : row.object.trigger_type === 'manual' ? '手动' : triggerLabel(row.object.trigger_type) + (triggerDetail(row.object) ? ' · ' + triggerDetail(row.object) : '') }}</span>
              <span v-if="row.kind === 'task'" class="status-badge"><i class="status-dot" :class="row.object.last_status"></i>最近：{{ statusLabel(row.object.last_status) }}</span>
              <span v-else class="status-badge"><i class="status-dot" :class="row.object.last_run?.status"></i>最近：{{ workflowStatusLabel(row.object.last_run?.status) }}</span>
            </div>
            <template v-if="row.kind === 'task'">
              <div class="center-object-facts"><span :class="'env-' + row.object.environment_status">{{ row.object.target_host_id ? '运行时准备环境' : envStatusLabel(row.object.environment_status) }}</span><span>归属人：{{ row.object.created_by_name || '系统迁移' }}</span><span v-if="row.object.trigger_type !== 'manual'">下次运行：{{ !row.object.enabled ? '停用后不调度' : row.object.first_environment_ready === 0 && !row.object.target_host_id ? '依赖准备中' : row.object.next_run_at ? formatTime(row.object.next_run_at) : '未知' }}</span></div>
              <p v-if="row.object.environment_status === 'failed' && row.object.environment_error" class="center-description danger-text">{{ row.object.environment_error }}</p>
            </template>
            <template v-else>
              <div class="center-object-facts"><span>{{ row.object.archived ? '已归档' : row.object.steps?.some(step => step.available === false) ? '成员不可用' : '成员可用' }}</span><span v-if="row.object.schedule_enabled">下次运行：{{ row.object.next_run_at ? formatTime(row.object.next_run_at) : '未知' }}</span></div>
              <ol class="center-flow-steps" aria-label="执行顺序"><li v-for="step in row.object.steps" :key="step.position" :class="{ 'danger-text': step.available === false }"><span>{{ step.position }}</span>{{ step.task_name }}{{ step.available === false ? '（不可用）' : '' }}</li></ol>
            </template>
          </div>
          <div class="center-object-actions">
            <template v-if="row.kind === 'task'">
              <button class="plan-state" :class="{ enabled: row.object.enabled }" type="button" @click="toggleTask(row.object)"><i></i>{{ row.object.enabled ? '已启用' : '已停用' }}</button>
              <button v-if="taskIsActive(row.object)" class="button secondary compact" type="button" @click="viewTaskExecution(row.object)">查看运行</button>
              <button v-else class="button secondary compact" type="button" :disabled="!row.object.enabled || (row.object.target_host_id ? !isAdmin : row.object.environment_status !== 'ready')" :title="row.object.target_host_id && !isAdmin ? '远程任务需由管理员运行' : '将在 ' + (row.object.target_host_name || '主控本机') + ' 运行'" @click="runTask(row.object)">运行</button>
              <button class="button ghost compact" type="button" :disabled="taskIsActive(row.object)" @click="openEdit(row.object)">编辑</button>
              <details v-if="isAdmin" class="center-more"><summary>更多</summary><div class="center-more-actions"><button class="button ghost compact" type="button" @click="choosePlan(row)">设置定时</button>
              <button v-if="isAdmin" class="button ghost compact" type="button" @click="openAi(row.object)">AI 助手</button>
              <button v-if="isAdmin && row.object.environment_status === 'failed'" class="button ghost compact" type="button" @click="rebuildTaskEnvironment(row.object)">修复环境</button>
              <button v-if="isAdmin" class="button ghost compact archive-button" type="button" title="同时删除程序、独立环境和运行记录；流程简要历史按原规则保留" @click="deletingTask = row.object">删除</button></div></details>
            </template>
            <template v-else>
              <button v-if="row.object.active_run_id" class="button secondary compact" type="button" @click="openRun(row.object.active_run_id)">查看运行</button>
              <button v-else-if="row.object.last_run" class="button ghost compact" type="button" @click="openRun(row.object.last_run.id)">查看最近结果</button>
              <button v-if="isAdmin && !row.object.archived" class="button secondary compact" type="button" :disabled="Boolean(busy) || (Boolean(row.object.active_run_id) && !pendingStarts.has(row.object.id))" @click="startWorkflow(row.object)">{{ busy === 'start:' + row.object.id ? '正在提交…' : pendingStarts.has(row.object.id) ? '重试启动' : '运行' }}</button>
              <button v-if="isAdmin && !row.object.archived" class="button ghost compact" type="button" :disabled="Boolean(busy)" @click="openEditor(row.object)">编辑</button>
              <details class="center-more"><summary>更多</summary><div class="center-more-actions"><button v-if="isAdmin && !row.object.archived" class="button ghost compact" type="button" :disabled="Boolean(busy)" @click="choosePlan(row)">设置定时</button>
              <button class="button ghost compact" type="button" @click="triggerTarget = row.object">触发记录</button>
              <button v-if="isAdmin && !row.object.archived" class="button ghost compact archive-button" type="button" :disabled="Boolean(busy) || Boolean(row.object.active_run_id)" @click="askConfirmation({ type: 'archive', id: row.id, name: row.object.name })">归档</button></div></details>
            </template>
          </div>
        </li>
      </ul>
      <div v-else class="empty-state compact-empty"><strong>{{ !complete ? '当前没有已载入的匹配对象' : tasks.length || workflows.length ? '没有匹配的任务或工作流' : '还没有任务或工作流' }}</strong><p>{{ !complete ? '部分资料仍未取得，请重试读取。' : tasks.length || workflows.length ? '调整筛选条件后再试。' : '从“新建”选择单任务或工作流。' }}</p></div>
    </section>
    <CreationDialog v-if="createObjectOpen && isAdmin" @created="createdObject" />
    <WorkflowEditor v-if="editor" :state="state" />
    <WorkflowRunDetail v-if="selectedRunId && !confirmation" :state="state" :can-manage="isAdmin" @execution="execution" />
    <WorkflowDialog v-if="confirmation" :title="confirmation.type === 'stop' ? '停止本轮流程' : '归档流程'" :close-disabled="Boolean(busy)" @close="closeConfirmation">
      <p v-if="confirmation.type === 'stop'">停止「{{ confirmation.name }}」本轮运行。后续步骤立即取消，当前步骤完成收尾后结束本轮。</p><p v-else>归档「{{ confirmation.name }}」。已有历史记录和成员任务保留，归档后不能新建运行。</p>
      <div v-if="actionError" class="center-feedback error" role="alert">{{ actionError }}</div>
      <template #footer><button class="button secondary" type="button" :disabled="Boolean(busy)" @click="closeConfirmation">取消</button><button class="button danger" type="button" :disabled="Boolean(busy)" @click="confirmAction">{{ busy ? '正在提交…' : confirmation.type === 'stop' ? '停止本轮' : '归档流程' }}</button></template>
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
.center-filter-bar input, .center-filter-bar select { font-size: 13px; }
.center-count { color: var(--muted); font-size: 12px; }
.center-header-actions, .center-object-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.center-archive-filter { display: flex; gap: 6px; align-items: center; color: var(--muted); font-size: 11px; }
.center-object-list { margin: 0; padding: 0; list-style: none; }
.center-object-list > li { display: grid; grid-template-columns: minmax(0, 1fr) minmax(220px, 340px); gap: 20px; padding: 16px 18px; border-bottom: 1px solid var(--border-soft); }
.center-object-list > li:last-child { border-bottom: 0; }
.center-object-main { display: grid; gap: 8px; min-width: 0; }
.center-object-name { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.center-object-name strong { font-size: 14px; overflow-wrap: anywhere; }
.center-kind-badge { border: 1px solid var(--border); border-radius: 6px; padding: 3px 6px; color: var(--muted); background: var(--surface-subtle); font-size: 11px; }
.center-description { margin: 0; color: var(--muted); font-size: 12px; line-height: 20px; overflow-wrap: anywhere; }
.center-object-facts { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 16px; color: var(--muted); font-size: 11px; overflow-wrap: anywhere; }
.center-object-actions { align-content: center; justify-content: flex-end; }
.center-more { max-width: 100%; color: var(--muted); font-size: 13px; }
.center-more summary { cursor: pointer; padding: 7px 10px; border-radius: 8px; }
.center-more summary:hover { background: var(--surface-subtle); color: var(--text); }
.center-more summary:focus-visible { outline: 2px solid rgba(0,145,57,.3); outline-offset: 2px; }
.center-more[open] { flex-basis: 100%; }
.center-more[open] summary { width: fit-content; margin-left: auto; }
.center-more-actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 4px; padding-top: 4px; }
.center-flow-steps { display: flex; flex-wrap: wrap; gap: 6px 12px; margin: 0; padding: 0; list-style: none; }
.center-flow-steps li { display: inline-flex; gap: 6px; align-items: center; font-size: 12px; overflow-wrap: anywhere; }
.center-flow-steps li + li:before { content: '→'; color: var(--faint); }
.center-flow-steps span { display: grid; place-items: center; width: 20px; height: 20px; flex-shrink: 0; background: var(--surface-subtle); border-radius: 6px; color: var(--muted); font-size: 11px; }
.center-feedback { padding: 12px 14px; border: 1px solid var(--border); border-radius: 8px; color: var(--muted); font-size: 12px; line-height: 20px; overflow-wrap: anywhere; }
.center-feedback.error { border-color: #f0c2b7; background: var(--coral-soft); color: #ad4935; }
.center-feedback.success { border-color: #c9e2d2; background: var(--brand-soft); color: var(--brand-strong); }
@media (max-width: 1000px) { .center-object-list > li { grid-template-columns: minmax(0, 1fr); gap: 12px; } .center-object-actions { justify-content: flex-start; } .center-more[open] summary { margin-left: 0; } .center-more-actions { justify-content: flex-start; } }
@media (max-width: 720px) { .center-objects > .panel-heading { align-items: flex-start; flex-wrap: wrap; } .center-header-actions { width: 100%; } .center-header-actions .center-archive-filter { margin-right: auto; } }
@media (max-width: 420px) { .center-filter-bar > .filter-field { flex-basis: 100%; } .center-object-list > li { padding: 14px; } .center-object-actions { gap: 6px; } }
</style>
