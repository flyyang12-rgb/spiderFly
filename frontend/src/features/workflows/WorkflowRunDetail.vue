<script setup>
import { computed } from 'vue'
import { formatTime } from '../../lib/format'
import { activeWorkflowStates, workflowReasonPreview, workflowStatusLabel } from './useWorkflows'
import WorkflowDialog from './WorkflowDialog.vue'

const props = defineProps({ state: { type: Object, required: true }, canManage: Boolean })
const emit = defineEmits(['execution'])
const workflow = computed(() => props.state.workflows.value.find((flow) => flow.id === props.state.runDetail.value?.workflow_id))
const active = computed(() => activeWorkflowStates.has(props.state.runDetail.value?.status))
const steps = computed(() => [...(props.state.runDetail.value?.steps || [])].sort((a, b) => a.position - b.position))

function rerun() {
  if (workflow.value) props.state.startWorkflow(workflow.value, props.state.runDetail.value.id)
}
</script>

<template>
  <WorkflowDialog :title="'流程运行 #' + state.selectedRunId.value" wide @close="state.closeRun">
    <div v-if="state.detailError.value" class="workflow-feedback error" role="alert">{{ state.detailError.value }}<button class="text-button" type="button" @click="state.loadRunDetail(state.selectedRunId.value)">重试读取</button></div>
    <div v-if="state.detailLoading.value && !state.runDetail.value" class="workflow-loading" role="status"><span class="spinner"></span>正在读取流程运行…</div>
    <template v-if="state.runDetail.value">
      <div class="workflow-run-summary">
        <div><strong>{{ state.runDetail.value.workflow_name }}</strong><span class="status-badge"><i class="status-dot" :class="state.runDetail.value.status"></i>{{ workflowStatusLabel(state.runDetail.value.status) }}</span></div>
        <div v-if="state.runDetail.value.reason" class="workflow-run-reason">
          <p>{{ state.runDetail.value.reason.length > 180 ? workflowReasonPreview(state.runDetail.value.reason) : state.runDetail.value.reason }}</p>
          <details v-if="state.runDetail.value.reason.length > 180" class="workflow-full-reason"><summary>查看完整原因</summary><p>{{ state.runDetail.value.reason }}</p></details>
        </div>
        <p v-else-if="state.runDetail.value.status === 'stopping'" class="workflow-run-reason">正在等待当前步骤结束并完成清理，后续步骤已取消。</p>
        <dl>
          <div><dt>发起人</dt><dd>{{ state.runDetail.value.requested_by_name || '系统' }}</dd></div>
          <div><dt>触发来源</dt><dd>{{ state.runDetail.value.rerun_of ? '整轮重跑（原运行 #' + state.runDetail.value.rerun_of + '）' : state.runDetail.value.source === 'manual' ? '手动' : '计划' }}</dd></div>
          <div><dt>计划时间</dt><dd>{{ formatTime(state.runDetail.value.scheduled_for) }}</dd></div>
          <div><dt>提交时间</dt><dd>{{ formatTime(state.runDetail.value.created_at) }}</dd></div>
          <div><dt>开始时间</dt><dd>{{ formatTime(state.runDetail.value.started_at) }}</dd></div>
          <div><dt>结束时间</dt><dd>{{ formatTime(state.runDetail.value.ended_at) }}</dd></div>
          <div><dt>当前步骤</dt><dd>{{ active && state.runDetail.value.current_step ? '第 ' + state.runDetail.value.current_step + ' 步' : '—' }}</dd></div>
        </dl>
      </div>
      <ol class="workflow-run-steps" aria-label="本轮步骤">
        <li v-for="step in steps" :key="step.position">
          <span class="workflow-step-index">{{ step.position }}</span>
          <div class="workflow-step-body">
            <div class="workflow-step-heading"><strong>{{ step.task_name }}</strong><span class="status-badge"><i class="status-dot" :class="step.status"></i>{{ workflowStatusLabel(step.status) }}</span></div>
            <div class="workflow-step-meta"><span>版本 {{ step.task_version == null ? '未知' : 'v' + step.task_version }}</span><span>开始 {{ formatTime(step.started_at) }}</span><span>结束 {{ formatTime(step.ended_at) }}</span></div>
            <details v-if="step.error_summary && step.error_summary.length > 180" class="workflow-step-reason"><summary>查看步骤原因</summary><p>{{ step.error_summary }}</p></details>
            <p v-else-if="step.error_summary" class="workflow-step-reason">{{ step.error_summary }}</p>
            <button v-if="step.execution_id && step.execution_available === true" class="text-button workflow-log-link" type="button" @click="emit('execution', step.execution_id)">运行 #{{ step.execution_id }} · 日志与产物</button>
            <span v-else-if="step.execution_id" class="workflow-deleted-evidence">运行资料已删除，保留本轮摘要</span>
          </div>
        </li>
      </ol>
      <p v-if="!active" class="workflow-run-help">整轮重跑会按当前流程和任务版本新建运行。</p>
      <div v-if="state.actionError.value" class="workflow-feedback error" role="alert">{{ state.actionError.value }}</div>
    </template>
    <template #footer>
      <button class="button ghost" type="button" @click="state.closeRun">关闭</button>
      <button v-if="canManage && active && state.runDetail.value.status !== 'stopping'" class="button danger" type="button" :disabled="Boolean(state.busy.value)" @click="state.askConfirmation({ type: 'stop', id: state.runDetail.value.id, name: state.runDetail.value.workflow_name })">停止本轮</button>
      <button v-else-if="canManage && state.runDetail.value && !active && workflow && !workflow.archived" class="button primary" type="button" :disabled="Boolean(state.busy.value) || Boolean(workflow.active_run_id)" @click="rerun">{{ state.busy.value === 'start:' + workflow.id ? '正在提交…' : '整轮重跑' }}</button>
    </template>
  </WorkflowDialog>
</template>

<style scoped>
.workflow-run-summary { display: grid; min-width: 0; grid-template-columns: minmax(0, 1fr); gap: 16px; padding: 0 0 20px; border: 0; border-bottom: 1px solid var(--border-soft); border-radius: 0; background: transparent; }
.workflow-run-summary > div { display: flex; align-items: center; flex-wrap: wrap; justify-content: space-between; gap: 12px; }
.workflow-run-summary strong { font-size: 15px; overflow-wrap: anywhere; }
.workflow-run-reason { margin: 0; font-size: 12px; overflow-wrap: anywhere; }
.workflow-run-reason > p { margin: 0; }
.workflow-full-reason { width: 100%; margin-top: 8px; font-size: 12px; }
.workflow-full-reason summary { color: var(--muted); cursor: pointer; }
.workflow-full-reason p { max-height: 240px; overflow: auto; margin: 8px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.workflow-run-summary dl { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px 16px; margin: 0; }
.workflow-run-summary dl div { display: grid; gap: 4px; }
.workflow-run-summary dt { font-size: 11px; color: var(--muted); }
.workflow-run-summary dd { margin: 0; font-size: 12px; overflow-wrap: anywhere; }
.workflow-run-steps { display: grid; min-width: 0; grid-template-columns: minmax(0, 1fr); gap: 0; padding: 0; margin: 0; list-style: none; }
.workflow-run-steps > li { display: flex; align-items: flex-start; gap: 12px; padding: 16px 0; border: 0; border-bottom: 1px solid var(--border-soft); border-radius: 0; }
.workflow-step-index { display: grid; flex-shrink: 0; width: 28px; height: 28px; place-items: center; border-radius: 6px; background: var(--brand-soft); color: var(--brand-strong); font-size: 12px; }
.workflow-step-body { display: grid; flex: 1; min-width: 0; gap: 8px; }
.workflow-step-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.workflow-step-heading strong { font-size: 13px; overflow-wrap: anywhere; }
.workflow-step-meta { display: flex; flex-wrap: wrap; gap: 8px 16px; color: var(--muted); font-size: 11px; }
.workflow-step-reason { margin: 0; color: var(--text); font-size: 12px; line-height: 20px; white-space: pre-wrap; overflow-wrap: anywhere; }
.workflow-step-reason summary { color: var(--muted); cursor: pointer; }
.workflow-step-reason p { margin: 8px 0 0; }
.workflow-log-link { justify-self: start; padding-left: 0; text-align: left; white-space: normal; }
.workflow-deleted-evidence, .workflow-run-help { color: var(--muted); font-size: 11px; line-height: 18px; }
.workflow-run-help { margin: 0; }
.workflow-feedback { padding: 12px; border: 1px solid #f0c2b7; border-radius: 8px; background: var(--coral-soft); color: #ad4935; font-size: 12px; overflow-wrap: anywhere; }
.workflow-loading { display: flex; align-items: center; justify-content: center; gap: 8px; padding: 32px; font-size: 12px; }
.status-dot.waiting, .status-dot.stopping { border-color: var(--blue); background: var(--blue); }
.status-dot.skipped { border-color: var(--faint); background: var(--faint); }
@media (max-width: 720px) { .workflow-run-summary dl { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 420px) { .workflow-run-summary dl { grid-template-columns: minmax(0, 1fr); } .workflow-run-steps > li { padding: 12px 0; gap: 8px; } }
</style>
