<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { request } from '../../lib/api'
import { useWorkspaceContext } from '../../workspace/context'
import CreateTaskPage from '../apps/CreateTaskPage.vue'
import WorkflowDialog from '../workflows/WorkflowDialog.vue'
import PlanFields from '../schedule/PlanFields.vue'
import { emptyPlan, planBody, validatePlan, creationRequestId } from '../../lib/plans'

const emit = defineEmits(['created'])
const { me, isAdmin, appForm, createObjectOpen, createObjectKind, uploadApp, uploading, uploadUncertain,
  requirementsReading, resetCreateSession, handleSessionExpired, resolveUpload, showToast } = useWorkspaceContext()
const flow = reactive({ name: '', description: '', task_ids: [], ...emptyPlan() })
let pendingFlow = null
const flowError = ref('')
const flowSaving = ref(false)
const flowUncertain = ref(false)
const candidates = ref([])
const candidatesError = ref('')
const candidatesLoading = ref(false)
const selectedTask = ref('')
const created = ref(null)
const busy = computed(() => uploading.value || flowSaving.value)
const selectedSteps = computed(() => flow.task_ids.map(id => candidates.value.find(task => Number(task.id) === id)
  || { id, name: `任务 #${id}`, eligible: false, unavailable_reason: '任务已删除或不可用' }))
let disposed = false
let generation = 0
let candidatesSequence = 0
const controllers = new Set()
function live(version, owner) { return !disposed && generation === version && Boolean(owner) && me.value === owner }
async function api(path, options = {}) {
  const controller = new AbortController()
  controllers.add(controller)
  try { return await request(path, { ...options, signal: controller.signal }) }
  finally { controllers.delete(controller) }
}
async function loadCandidates() {
  if (!isAdmin.value || !me.value || busy.value) return
  const sequence = ++candidatesSequence
  const version = generation
  const owner = me.value
  candidatesLoading.value = true
  try {
    const result = await api('/workflows/tasks')
    if (!live(version, owner) || sequence !== candidatesSequence) return
    if (!Array.isArray(result)) throw new Error('流程候选任务响应不完整，请重试读取')
    candidates.value = result.map(task => ({ ...task, eligible: Boolean(task.eligible ?? task.available), unavailable_reason: task.unavailable_reason ?? task.reason ?? '' }))
    candidatesError.value = ''
  } catch (error) {
    if (!live(version, owner) || sequence !== candidatesSequence || error.name === 'AbortError') return
    candidatesError.value = error.message || '无法读取可选任务，请重试'
    if (live(version, owner) && error.status === 401) handleSessionExpired()
  } finally { if (live(version, owner) && sequence === candidatesSequence) candidatesLoading.value = false }
}
function addStep() {
  if (busy.value || flowUncertain.value || candidatesError.value || flow.task_ids.length >= 100) return
  const id = Number(selectedTask.value)
  if (!candidates.value.find(task => Number(task.id) === id)?.eligible || flow.task_ids.includes(id)) return
  flow.task_ids.push(id)
  selectedTask.value = ''
  flowError.value = ''
}
function moveStep(index, offset) {
  if (busy.value || flowUncertain.value) return
  const target = index + offset
  if (target < 0 || target >= flow.task_ids.length) return
  const updated = [...flow.task_ids]
  ;[updated[index], updated[target]] = [updated[target], updated[index]]
  flow.task_ids = updated
}
function switchKind(kind) { if (!busy.value && !created.value) createObjectKind.value = kind }
function close() { if (!busy.value) createObjectOpen.value = false }
function complete(target) {
  created.value = target
  emit('created', target)
  createObjectOpen.value = false
}
async function submit(retryUncertain = false) {
  if (!isAdmin.value || busy.value || created.value) return
  const version = generation
  const owner = me.value
  if (createObjectKind.value === 'task') {
    const result = await uploadApp({ retryUncertain })
    if (!result || !live(version, owner)) return
    complete(result)
    return
  }
  if (flowUncertain.value && !retryUncertain) return
  flowError.value = ''
  if (!flow.name.trim()) { flowError.value = '请填写流程名称'; return }
  if (!flow.task_ids.length) { flowError.value = '请至少添加一个任务'; return }
  if (new Set(flow.task_ids).size !== flow.task_ids.length) { flowError.value = '同一任务不能重复添加'; return }
  if (candidatesLoading.value || candidatesError.value) { flowError.value = '可选任务尚未读取完成，请重试读取后创建'; return }
  const unavailable = selectedSteps.value.find(task => !task.eligible)
  if (unavailable) { flowError.value = `${unavailable.name}不可用于本机流程：${unavailable.unavailable_reason || '任务已删除或不符合运行范围'}`; return }
  const planError = validatePlan(flow)
  if (planError) { flowError.value = planError; return }
  pendingFlow ||= { name: flow.name.trim(), description: flow.description.trim(), task_ids: [...flow.task_ids],
    schedule: planBody(flow, true), request_id: creationRequestId() }
  flowSaving.value = true
  try {
    const result = await api('/workflows', { method: 'POST', body: JSON.stringify(pendingFlow) })
    if (!live(version, owner)) return
    if (!result?.id) throw new Error('服务器未返回新流程身份，请先刷新任务中心确认创建结果')
    showToast('success', '工作流已保存')
    complete({ kind: 'workflow', id: Number(result.id), object: result })
  } catch (error) {
    if (!live(version, owner) || error.name === 'AbortError') return
    flowUncertain.value = !error.status || error.status >= 500
    if (!flowUncertain.value) pendingFlow = null
    flowError.value = flowUncertain.value
      ? `创建结果未确认，请先刷新任务中心检查是否已有该流程，避免重复创建。${error.message || ''}`
      : error.message || '创建流程失败，请检查输入'
    if (live(version, owner) && error.status === 401) handleSessionExpired()
  } finally { if (live(version, owner)) flowSaving.value = false }
}
async function recover() {
  if (busy.value) return
  const version = generation, owner = me.value
  if (createObjectKind.value === 'task') {
    const result = await resolveUpload()
    if (result && live(version, owner)) complete(result)
    return
  }
  if (!pendingFlow) return
  flowSaving.value = true
  try {
    const result = await api('/creation-requests/workflow/' + pendingFlow.request_id)
    if (live(version, owner)) complete(result)
  } catch (error) {
    if (live(version, owner) && error.name !== 'AbortError') flowError.value = error.message
    if (live(version, owner) && error.status === 401) handleSessionExpired()
  } finally { if (live(version, owner)) flowSaving.value = false }
}
watch(createObjectKind, kind => { if (kind === 'workflow' && !created.value) loadCandidates() })
watch(me, (user, previous) => {
  if (user !== previous) {
    generation += 1
    candidatesSequence += 1
    controllers.forEach(controller => controller.abort())
    Object.assign(flow, { name: '', description: '', task_ids: [], ...emptyPlan() })
    pendingFlow = null
    candidates.value = []
    selectedTask.value = ''; flowError.value = ''; candidatesError.value = ''; created.value = null
    flowSaving.value = false; flowUncertain.value = false; candidatesLoading.value = false
    createObjectOpen.value = false
  }
}, { flush: 'sync' })
onMounted(() => { if (createObjectKind.value === 'workflow') loadCandidates() })
onBeforeUnmount(() => { disposed = true; generation += 1; controllers.forEach(controller => controller.abort()); resetCreateSession() })
</script>

<template>
  <WorkflowDialog title="新建" :close-disabled="busy" @close="close">
      <fieldset class="creation-kind" :disabled="busy"><legend>类型</legend>
        <button type="button" :class="{ active: createObjectKind === 'task' }" :aria-pressed="createObjectKind === 'task'" @click="switchKind('task')">单任务</button>
        <button type="button" :class="{ active: createObjectKind === 'workflow' }" :aria-pressed="createObjectKind === 'workflow'" @click="switchKind('workflow')">工作流</button>
      </fieldset>
      <CreateTaskPage v-show="createObjectKind === 'task'" embedded form-id="unified-create-task" :disabled="busy" @submit="submit()" />
      <form v-show="createObjectKind === 'workflow'" id="unified-create-workflow" class="creation-flow" @submit.prevent="submit()">
        <fieldset :disabled="busy || flowUncertain">
          <legend class="sr-only">工作流设置</legend>
          <div v-if="flowError" class="creation-feedback error" role="alert">{{ flowError }}</div>
          <div class="creation-flow-basics"><label class="field"><span>流程名称</span><input v-model="flow.name" type="text" maxlength="100" required placeholder="例如：报表下载与入库" /></label>
          <label class="field"><span>流程说明 <small>可选</small></span><textarea v-model="flow.description" maxlength="500" rows="2" placeholder="说明流程用途"></textarea></label>
          </div>
          <p class="creation-help">主控本机。多个脚本先分别创建单任务，再按顺序添加。</p>
          <section class="creation-step-editor" aria-label="工作流步骤">
            <div class="creation-step-heading"><strong>执行顺序</strong><span>{{ flow.task_ids.length }} 个任务</span></div>
            <p class="creation-help">前一步成功并完成收尾后继续；最多 100 步。</p>
            <div v-if="candidatesError" class="creation-feedback error" role="alert">{{ candidatesError }}<button class="text-button" type="button" @click="loadCandidates">重试读取</button></div>
            <div class="creation-add-step"><label class="field"><span>已有任务</span>
              <select v-model="selectedTask" :disabled="candidatesLoading || Boolean(candidatesError)"><option value="">{{ candidatesLoading ? '正在读取任务…' : '选择已有任务' }}</option><option v-for="task in candidates" :key="task.id" :value="String(task.id)" :disabled="!task.eligible || flow.task_ids.includes(Number(task.id))">{{ task.name }}{{ flow.task_ids.includes(Number(task.id)) ? '（已添加）' : task.eligible ? '' : '（' + task.unavailable_reason + '）' }}</option></select>
            </label><button class="button secondary" type="button" :disabled="!selectedTask || flow.task_ids.length >= 100 || Boolean(candidatesError)" @click="addStep">添加步骤</button></div>
            <details class="creation-rules"><summary>可用任务要求</summary><p class="creation-help">仅支持已启用、环境就绪的本机普通 Python 任务。</p></details>
            <ol v-if="selectedSteps.length" class="creation-steps"><li v-for="(task, index) in selectedSteps" :key="task.id"><span class="creation-step-number">{{ index + 1 }}</span><div class="creation-step-copy"><strong>{{ task.name }}</strong><small v-if="!task.eligible" class="danger-text">{{ task.unavailable_reason }}</small></div><div class="creation-step-actions"><button class="icon-button" type="button" :aria-label="'上移 ' + task.name" :disabled="index === 0" @click="moveStep(index, -1)">↑</button><button class="icon-button" type="button" :aria-label="'下移 ' + task.name" :disabled="index === flow.task_ids.length - 1" @click="moveStep(index, 1)">↓</button><button class="icon-button" type="button" :aria-label="'移除 ' + task.name" @click="flow.task_ids.splice(index, 1)">×</button></div></li></ol>
            <p v-else class="creation-empty">尚未添加步骤</p>
          </section>
          <PlanFields :form="flow" :disabled="busy || flowUncertain" @unauthorized="handleSessionExpired" />
        </fieldset>
      </form>
      <div v-if="createObjectKind === 'task' ? uploadUncertain : flowUncertain" class="creation-recovery"><button type="button" class="button secondary" :disabled="busy" @click="recover">核对创建结果</button><button type="button" class="text-button" :disabled="busy" @click="submit(true)">原请求重试</button></div>
    <template #footer>
      <button class="button secondary" type="button" :disabled="busy" @click="close">取消</button>
      <button class="button primary" type="submit" :form="createObjectKind === 'task' ? 'unified-create-task' : 'unified-create-workflow'" :disabled="busy || (createObjectKind === 'task' ? uploadUncertain || requirementsReading || Boolean(appForm?.dependency_status && !['ready', 'idle'].includes(appForm.dependency_status)) : flowUncertain || candidatesLoading || Boolean(candidatesError))">{{ busy ? '正在保存…' : '保存' }}</button>
    </template>
  </WorkflowDialog>
</template>

<style scoped>
.creation-kind { margin: 0 0 16px; padding: 0; border: 0; display: flex; gap: 8px; flex-wrap: wrap; }
.creation-kind legend { margin-bottom: 8px; font-size: 12px; }
.creation-kind button { padding: 8px 14px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); color: var(--text); font-size: 13px; cursor: pointer; }
.creation-kind button.active { color: var(--brand-strong); background: var(--brand-soft); border-color: var(--brand); }
.creation-flow, .creation-flow > fieldset { display: grid; gap: 16px; min-width: 0; grid-template-columns: minmax(0, 1fr); }
.creation-flow > fieldset { margin: 0; padding: 0; border: 0; width: 100%; }
.creation-flow-basics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.creation-flow textarea { width: 100%; min-width: 0; resize: vertical; border: 1px solid var(--border); border-radius: 8px; padding: 10px 11px; color: var(--text); font: inherit; font-size: 13px; }
.creation-kind button:focus-visible, .creation-flow textarea:focus-visible { outline: 2px solid rgba(0,145,57,.3); outline-offset: 2px; }
.creation-help { margin: 0; color: var(--muted); font-size: 12px; line-height: 20px; overflow-wrap: anywhere; }
.creation-step-editor { display: grid; min-width: 0; grid-template-columns: minmax(0, 1fr); gap: 12px; padding: 16px 0 0; border: 0; border-top: 1px solid var(--border-soft); border-radius: 0; }
.creation-step-heading { display: flex; justify-content: space-between; gap: 12px; font-size: 13px; }
.creation-step-heading span { font-size: 11px; color: var(--muted); }
.creation-add-step { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: 8px; }
.creation-steps { margin: 0; padding: 0; list-style: none; display: grid; gap: 8px; }
.creation-steps > li { display: flex; align-items: center; gap: 12px; padding: 12px; border: 1px solid var(--border-soft); border-radius: 8px; background: var(--surface-subtle); }
.creation-step-number { flex-shrink: 0; width: 24px; height: 24px; display: grid; place-items: center; background: var(--brand-soft); color: var(--brand-strong); font-size: 12px; border-radius: 6px; }
.creation-step-copy { flex: 1; display: grid; min-width: 0; gap: 4px; }
.creation-step-copy strong { font-size: 13px; overflow-wrap: anywhere; }
.creation-step-copy small { font-size: 11px; }
.creation-step-actions { display: flex; gap: 4px; }
.creation-rules { color: var(--muted); font-size: 12px; }
.creation-rules summary { cursor: pointer; }
.creation-rules p { margin-top: 8px; }
.creation-empty { margin: 0; padding: 12px 0; color: var(--muted); border: 0; border-radius: 0; font-size: 12px; }
.creation-feedback.error { padding: 12px; border: 1px solid #f0c2b7; border-radius: 8px; background: var(--coral-soft); color: #ad4935; font-size: 12px; overflow-wrap: anywhere; }
.creation-recovery { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 12px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
@media (max-width: 520px) { .creation-flow-basics, .creation-add-step { grid-template-columns: minmax(0, 1fr); } .creation-steps > li { flex-wrap: wrap; gap: 8px; } .creation-step-actions { margin-left: auto; } }
</style>
