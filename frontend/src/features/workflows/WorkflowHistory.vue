<script setup>
import { nextTick } from 'vue'
import { useWorkspaceContext } from '../../workspace/context'
import FilterSection from '../../components/FilterSection.vue'
import { formatTime } from '../../lib/format'
import { activeWorkflowStates, useWorkflows, workflowReasonPreview, workflowStatusLabel } from './useWorkflows'
import WorkflowDialog from './WorkflowDialog.vue'
import WorkflowRunDetail from './WorkflowRunDetail.vue'

const { isAdmin, me, handleSessionExpired, openExecution, workflowPendingStarts } = useWorkspaceContext()
const state = useWorkflows({ isAdmin, me, onSessionExpired: handleSessionExpired, workflowPendingStarts, mode: 'history' })
const {
  workflows, runs, total, page, pageSize, totalPages, loading, refreshing, historyLoading,
  loadError, historyError, actionError, notice, busy, filters, selectedRunId, confirmation,
  refresh, loadHistory, applyFilters, resetFilters, openRun, confirmAction,
} = state

function closeConfirmation() { if (!busy.value) confirmation.value = null }
async function openStepExecution(id) {
  const user = me.value
  state.closeRun()
  await nextTick()
  if (!user || me.value !== user) return
  const pending = openExecution({ id })
  await nextTick()
  if (me.value === user) document.querySelector('.log-modal .modal-close')?.focus()
  await pending
}
</script>

<template>
  <section class="view-stack workflow-history-page">
    <div v-if="notice" class="workflow-feedback success" role="status">{{ notice }}</div>
    <div v-if="actionError" class="workflow-feedback error" role="alert">{{ actionError }}</div>
    <div v-if="loadError" class="workflow-feedback error" role="alert">流程筛选项暂未完整载入：{{ loadError }}<button class="text-button" type="button" :disabled="refreshing" @click="refresh()">重试读取</button></div>
    <section class="panel table-panel" aria-label="流程整轮记录">
      <header class="panel-heading">
        <div class="history-heading"><p class="history-count" role="status">{{ historyError ? '记录尚未完整载入' : historyLoading || loading ? '正在查询记录…' : total + ' 条流程整轮' }}</p><slot name="scope-help" /></div>
        <button class="button secondary compact" type="button" :disabled="historyLoading || refreshing" @click="refresh()">{{ refreshing ? '刷新中…' : '刷新' }}</button>
      </header>
      <FilterSection :active="Boolean(filters.workflow_id || filters.status !== 'all' || filters.date_from || filters.date_to)">
      <form class="filter-bar workflow-filter-bar history-filter-bar" @submit.prevent="applyFilters">
        <slot name="scope" />
        <label class="filter-field"><span>流程</span><select v-model="filters.workflow_id"><option value="">全部流程</option><option v-for="flow in workflows" :key="flow.id" :value="String(flow.id)">{{ flow.name }}{{ flow.archived ? '（已归档）' : '' }}</option></select></label>
        <label class="filter-field"><span>运行状态</span><select v-model="filters.status"><option value="all">全部状态</option><option value="waiting">等待中</option><option value="running">运行中</option><option value="stopping">停止中</option><option value="success">成功</option><option value="failed">失败</option><option value="cancelled">已取消</option></select></label>
        <div class="history-date-range">
        <label class="filter-field"><span>开始日期</span><input v-model="filters.date_from" type="date" /></label>
        <label class="filter-field"><span>结束日期</span><input v-model="filters.date_to" type="date" /></label>
        </div>
        <div class="filter-actions"><button class="button primary compact" type="submit" :disabled="historyLoading">{{ historyLoading ? '查询中…' : '查询' }}</button><button class="button secondary compact" type="button" :disabled="historyLoading" @click="resetFilters">清空筛选</button></div>
      </form>
      </FilterSection>
      <div v-if="historyError" class="workflow-feedback error workflow-history-error" role="alert">{{ historyError }}{{ runs.length ? '；下方保留上次载入的记录。' : '' }}<button class="text-button" type="button" :disabled="historyLoading" @click="loadHistory()">重试读取</button></div>
      <div v-if="runs.length" class="table-wrap">
        <table>
          <thead><tr><th>流程与状态</th><th>发起人</th><th>提交时间</th><th>结束时间</th><th>结果或等待原因</th><th class="align-right">操作</th></tr></thead>
          <tbody>
            <tr v-for="run in runs" :key="'workflow-run:' + run.id">
              <td><div class="workflow-history-name"><strong>{{ run.workflow_name }}</strong><span class="status-badge"><i class="status-dot" :class="run.status"></i>{{ workflowStatusLabel(run.status) }} · #{{ run.id }}</span></div></td>
              <td>{{ run.requested_by_name || '系统' }}</td><td>{{ formatTime(run.created_at) }}</td><td>{{ formatTime(run.ended_at) }}</td>
              <td class="workflow-history-reason">
                <template v-if="run.reason && run.reason.length > 180"><span>{{ workflowReasonPreview(run.reason) }}</span><details class="workflow-full-reason"><summary>查看完整原因</summary><p>{{ run.reason }}</p></details></template>
                <template v-else>{{ run.reason || (activeWorkflowStates.has(run.status) && run.current_step ? '当前第 ' + run.current_step + ' 步' : '—') }}</template>
              </td>
              <td class="align-right"><button class="text-button" type="button" @click="openRun(run.id)">查看步骤</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty-state compact-empty"><strong>{{ historyLoading ? '正在读取运行记录…' : historyError ? '暂时无法读取记录' : '没有匹配的流程运行' }}</strong></div>
      <footer v-if="total" class="pagination-bar"><span>第 {{ (page - 1) * pageSize + 1 }}–{{ Math.min(page * pageSize, total) }} 条，共 {{ total }} 条</span><div><button class="button secondary compact" type="button" :disabled="page <= 1 || historyLoading" @click="loadHistory({ targetPage: page - 1 })">上一页</button><strong>{{ page }} / {{ totalPages }}</strong><button class="button secondary compact" type="button" :disabled="page >= totalPages || historyLoading" @click="loadHistory({ targetPage: page + 1 })">下一页</button></div></footer>
    </section>
    <WorkflowRunDetail v-if="selectedRunId" :state="state" :can-manage="isAdmin" @execution="openStepExecution" />
    <WorkflowDialog v-if="confirmation" title="停止本轮流程" :close-disabled="Boolean(busy)" @close="closeConfirmation">
      <p>停止「{{ confirmation.name }}」的本轮运行。后续步骤立即取消，当前步骤完成收尾后结束本轮。</p>
      <div v-if="actionError" class="workflow-feedback error" role="alert">{{ actionError }}</div>
      <template #footer><button class="button ghost" type="button" :disabled="Boolean(busy)" @click="closeConfirmation">取消</button><button class="button danger" type="button" :disabled="Boolean(busy)" @click="confirmAction">{{ busy ? '正在提交…' : '停止本轮' }}</button></template>
    </WorkflowDialog>
  </section>
</template>

<style scoped>
.history-count { margin: 0; color: var(--muted); font-size: 12px; }
.workflow-history-page { min-width: 0; grid-template-columns: minmax(0, 1fr); }
.panel, .table-wrap { min-width: 0; max-width: 100%; }
.table-wrap { overflow-x: auto; }
.workflow-feedback { display: block; padding: 12px 14px; border: 1px solid var(--border); border-radius: 8px; font-size: 12px; line-height: 20px; overflow-wrap: anywhere; }
.workflow-feedback.error { border-color: #f0c2b7; background: var(--coral-soft); color: #ad4935; }
.workflow-feedback.success { border-color: #c9e2d2; background: var(--brand-soft); color: var(--brand-strong); }
.workflow-history-error { margin: 0 18px 16px; }
.workflow-history-name { display: grid; min-width: 130px; gap: 6px; }
.workflow-history-name strong { font-size: 12px; }
.workflow-history-reason { max-width: 260px; overflow-wrap: anywhere; }
.workflow-full-reason { margin-top: 6px; font-size: 11px; }
.workflow-full-reason summary { color: var(--muted); cursor: pointer; }
.workflow-full-reason p { max-height: 240px; overflow: auto; margin: 8px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.status-dot.waiting, .status-dot.stopping { border-color: var(--blue); background: var(--blue); }
@media (max-width: 720px) { .panel-heading, .pagination-bar { align-items: flex-start; flex-wrap: wrap; } }
</style>
