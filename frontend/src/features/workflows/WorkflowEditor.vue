<script setup>
import WorkflowDialog from './WorkflowDialog.vue'
import PlanFields from '../schedule/PlanFields.vue'

const props = defineProps({ state: { type: Object, required: true } })
</script>

<template>
  <WorkflowDialog :title="state.editor.value.id ? '编辑流程' : '新建流程'" :close-disabled="state.busy.value === 'save'" @close="state.closeEditor">
    <form id="workflow-form" class="workflow-form" @submit.prevent="state.saveWorkflow()">
      <div v-if="state.formError.value" class="workflow-feedback error" role="alert">{{ state.formError.value }}</div>
      <fieldset class="workflow-fields" :disabled="state.busy.value === 'save' || state.formUncertain.value"><legend class="sr-only">工作流设置</legend>
      <div class="workflow-basics"><label class="field"><span>流程名称</span><input v-model="state.form.name" type="text" maxlength="100" required :disabled="state.busy.value === 'save'" placeholder="例如：报表下载与入库" /></label>
      <label class="field"><span>流程说明 <small>可选</small></span><textarea v-model="state.form.description" maxlength="500" rows="2" :disabled="state.busy.value === 'save'" placeholder="说明流程用途" /></label>
      </div>
      <div class="workflow-mode"><span>运行电脑：主控本机</span></div>
      <section class="workflow-step-editor" aria-label="流程步骤">
        <div class="workflow-section-heading"><strong>执行顺序</strong><span>{{ state.form.task_ids.length }} 个任务</span></div>
        <p class="workflow-help">前一步成功并完成收尾后继续；最多 100 步。</p>
        <div v-if="state.candidatesError.value" class="workflow-feedback error" role="alert">
          {{ state.candidatesError.value }}
          <button class="text-button" type="button" @click="state.loadCandidates">重试读取任务</button>
        </div>
        <div class="workflow-add-step">
          <label class="field"><span>已有任务</span>
            <select v-model="state.selectedTask.value" :disabled="state.candidatesLoading.value || state.busy.value === 'save'">
              <option value="">{{ state.candidatesLoading.value ? '正在读取任务…' : '选择任务' }}</option>
              <option v-for="task in state.candidates.value" :key="task.id" :value="String(task.id)" :disabled="!task.eligible || state.form.task_ids.includes(Number(task.id))">
                {{ task.name }}{{ state.form.task_ids.includes(Number(task.id)) ? '（已添加）' : task.eligible ? '' : '（' + task.unavailable_reason + '）' }}
              </option>
            </select>
          </label>
          <button class="button secondary" type="button" :disabled="!state.selectedTask.value || state.form.task_ids.length >= 100 || state.busy.value === 'save'" @click="state.addStep">添加步骤</button>
        </div>
        <details class="workflow-rules"><summary>可用任务要求</summary><p class="workflow-help">仅支持已启用、环境就绪的本机普通 Python 任务。</p></details>
        <ol v-if="state.selectedSteps.value.length" class="workflow-edit-steps">
          <li v-for="(task, index) in state.selectedSteps.value" :key="task.id">
            <span class="workflow-step-index">{{ index + 1 }}</span>
            <div class="workflow-step-copy"><strong>{{ task.name }}</strong><small v-if="!task.eligible" class="danger-text">{{ task.unavailable_reason }}</small></div>
            <div class="workflow-step-actions">
              <button class="icon-button" type="button" :aria-label="'上移 ' + task.name" :disabled="index === 0 || state.busy.value === 'save'" @click="state.moveStep(index, -1)">↑</button>
              <button class="icon-button" type="button" :aria-label="'下移 ' + task.name" :disabled="index === state.form.task_ids.length - 1 || state.busy.value === 'save'" @click="state.moveStep(index, 1)">↓</button>
              <button class="icon-button danger-icon" type="button" :aria-label="'移除 ' + task.name" :disabled="state.busy.value === 'save'" @click="state.form.task_ids.splice(index, 1)">×</button>
            </div>
          </li>
        </ol>
        <div v-else class="workflow-empty-steps">尚未添加步骤</div>
      </section>
      <p v-if="state.editor.value.active_run_id" class="workflow-help">当前运行沿用原步骤与版本。</p>
      <PlanFields :form="state.form" :disabled="state.busy.value === 'save'" :identity="state.editor.value.id || 'new'" />
      </fieldset>
      <div v-if="state.formUncertain.value"><button class="button secondary" type="button" :disabled="Boolean(state.busy.value)" @click="state.resolveCreation">核对创建结果</button><button class="text-button" type="button" :disabled="Boolean(state.busy.value)" @click="state.saveWorkflow({ retryUncertain: true })">原请求重试</button></div>
    </form>
    <template #footer>
      <button class="button ghost" type="button" :disabled="state.busy.value === 'save'" @click="state.closeEditor">取消</button>
      <button class="button primary" type="submit" form="workflow-form" :disabled="Boolean(state.busy.value) || state.formUncertain.value">{{ state.busy.value === 'save' ? '正在保存…' : '保存' }}</button>
    </template>
  </WorkflowDialog>
</template>

<style scoped>
.workflow-form { display: grid; min-width: 0; grid-template-columns: minmax(0, 1fr); gap: 16px; }
.workflow-fields { display: grid; gap: 16px; margin: 0; padding: 0; border: 0; min-width: 0; }
.workflow-basics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
.workflow-form .field { min-width: 0; }
.field textarea { width: 100%; resize: vertical; min-height: 58px; border: 1px solid var(--border); border-radius: 8px; padding: 10px 11px; color: var(--text); font: inherit; font-size: 13px; }
.field textarea:focus { outline: 2px solid rgba(0,145,57,.25); outline-offset: 2px; }
.workflow-mode { display: flex; gap: 20px; flex-wrap: wrap; color: var(--muted); font-size: 12px; }
.workflow-step-editor { display: grid; min-width: 0; grid-template-columns: minmax(0, 1fr); gap: 12px; padding: 16px 0 0; border: 0; border-top: 1px solid var(--border-soft); border-radius: 0; }
.workflow-section-heading { display: flex; gap: 12px; justify-content: space-between; align-items: center; font-size: 13px; }
.workflow-section-heading span { color: var(--muted); font-size: 11px; }
.workflow-help { margin: 0; color: var(--muted); font-size: 12px; line-height: 20px; }
.workflow-add-step { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: end; }
.workflow-edit-steps { display: grid; gap: 8px; padding: 0; margin: 0; list-style: none; }
.workflow-edit-steps li { display: flex; align-items: center; gap: 12px; padding: 12px; border: 1px solid var(--border-soft); border-radius: 8px; background: var(--surface-subtle); }
.workflow-step-index { display: grid; width: 24px; height: 24px; flex-shrink: 0; place-items: center; border-radius: 6px; background: var(--brand-soft); color: var(--brand-strong); font-size: 12px; }
.workflow-step-copy { display: grid; min-width: 0; flex: 1; gap: 4px; }
.workflow-step-copy strong { font-size: 13px; overflow-wrap: anywhere; }
.workflow-step-copy small { font-size: 11px; }
.workflow-step-actions { display: flex; gap: 4px; }
.workflow-rules { color: var(--muted); font-size: 12px; }
.workflow-rules summary { cursor: pointer; }
.workflow-rules p { margin-top: 8px; }
.workflow-empty-steps { padding: 12px 0; border: 0; border-radius: 0; color: var(--muted); font-size: 12px; }
.workflow-feedback { padding: 12px; border: 1px solid #f0c2b7; border-radius: 8px; background: var(--coral-soft); color: #ad4935; font-size: 12px; overflow-wrap: anywhere; }
@media (max-width: 520px) {
  .workflow-basics { grid-template-columns: minmax(0, 1fr); }
  .workflow-add-step { grid-template-columns: minmax(0, 1fr); }
  .workflow-edit-steps li { flex-wrap: wrap; gap: 8px; }
  .workflow-step-actions { margin-left: auto; }
}
</style>
