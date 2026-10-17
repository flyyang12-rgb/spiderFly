<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { request } from '../../lib/api'
import { emptyPlan, readPlan, planBody, validatePlan } from '../../lib/plans'
import { useWorkspaceContext } from '../../workspace/context'
import WorkflowDialog from '../workflows/WorkflowDialog.vue'
import PlanFields from './PlanFields.vue'

const props = defineProps({ initialKind: { type: String, default: 'task' }, initialId: { type: Number, default: null }, lockedObject: Boolean, createdContext: Boolean })
const emit = defineEmits(['close', 'saved'])
const { me, isAdmin, handleSessionExpired, loadAll, showToast } = useWorkspaceContext()
const kind = ref(props.initialKind)
const selectedId = ref(props.initialId == null ? '' : String(props.initialId))
const tasks = ref([])
const workflows = ref([])
const optionErrors = reactive({ task: '', workflow: '' })
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const form = reactive(emptyPlan())
let originalPlan = ''
let editVersion = null
const objects = computed(() => kind.value === 'workflow' ? workflows.value : tasks.value)
const selected = computed(() => objects.value.find(item => String(item.id) === selectedId.value))
const displayedError = computed(() => error.value && props.createdContext ? '对象已创建，定时尚未保存：' + error.value : error.value)
const controllers = new Set()
let disposed = false
let loadSequence = 0

async function api(path, options = {}) {
  const controller = new AbortController()
  controllers.add(controller)
  try { return await request(path, { ...options, signal: controller.signal }) }
  finally { controllers.delete(controller) }
}
function fail(failure, target = error) {
  if (disposed || failure?.name === 'AbortError') return
  target.value = failure?.message || '连接失败，请重试'
  if (failure?.status === 401) handleSessionExpired()
}
async function loadOptions() {
  const sequence = ++loadSequence
  const owner = me.value
  loading.value = true
  error.value = ''
  try {
    const kinds = props.lockedObject ? [props.initialKind] : ['task', 'workflow']
    const results = await Promise.allSettled(kinds.map(type => api(type === 'workflow' ? '/workflows' : '/tasks')))
    if (disposed || sequence !== loadSequence || me.value !== owner) return
    results.forEach((result, index) => {
      const type = kinds[index]
      if (result.status === 'fulfilled') {
        if (type === 'workflow') workflows.value = result.value.filter(item => !item.archived)
        else tasks.value = result.value
        optionErrors[type] = ''
      } else {
        optionErrors[type] = result.reason?.message || '暂时没有载入'
        fail(result.reason)
      }
    })
    if (props.lockedObject && !selected.value && !error.value) error.value = '已创建对象暂时没有载入，请刷新确认；请勿重复新建。'
  } catch (failure) { if (sequence === loadSequence) fail(failure) }
  finally { if (!disposed && sequence === loadSequence && me.value === owner) loading.value = false }
}
async function refreshOptions() {
  const identity = kind.value + ':' + selectedId.value
  const draft = { ...form, weekly_days: [...form.weekly_days] }
  const version = editVersion
  await loadOptions()
  await nextTick()
  if (!disposed && identity === kind.value + ':' + selectedId.value) { Object.assign(form, draft); editVersion = version }
}
watch(kind, () => { selectedId.value = ''; error.value = '' })
watch(selected, item => {
  error.value = ''
  Object.assign(form, readPlan(item, kind.value === 'workflow'))
  originalPlan = JSON.stringify(planBody(form, true))
  editVersion = kind.value === 'workflow' ? item?.revision : item?.version
})
async function save() {
  if (!isAdmin.value || busy.value || loading.value || !selected.value || optionErrors[kind.value]) return
  const planError = validatePlan(form, kind.value === 'task' && Boolean(selected.value.target_host_id))
  if (planError) { error.value = planError; return }
  const item = selected.value
  const selection = kind.value
  const owner = me.value
  busy.value = true
  error.value = ''
  try {
    let schedule = planBody(form, selection === 'workflow')
    if (selection === 'workflow' && JSON.stringify(schedule) === originalPlan) schedule = {
      trigger_type: item.trigger_type, trigger_config: item.trigger_config, schedule_enabled: item.schedule_enabled,
    }
    const body = selection === 'workflow' ? { revision: editVersion, ...schedule } : { version: editVersion, ...schedule }
    await api(selection === 'workflow' ? `/workflows/${item.id}/schedule` : `/tasks/${item.id}`, { method: 'PATCH', body: JSON.stringify(body) })
    if (disposed || me.value !== owner) return
    showToast('success', `${selection === 'workflow' ? '工作流' : '任务'}定时已${form.trigger_type !== 'manual' ? '保存' : '关闭'}`)
    emit('saved')
    emit('close')
    loadAll()
  } catch (failure) { fail(failure) }
  finally { if (!disposed) busy.value = false }
}
onMounted(loadOptions)
watch(me, (owner, previous) => { if (owner !== previous) { disposed = true; for (const controller of controllers) controller.abort(); emit('close') } }, { flush: 'sync' })
onBeforeUnmount(() => { disposed = true; loadSequence++; for (const controller of controllers) controller.abort() })
</script>

<template>
  <WorkflowDialog title="设置定时" :close-disabled="busy" @close="emit('close')">
    <form id="unified-plan-form" class="plan-form" @submit.prevent="save">
      <p v-if="createdContext" class="plan-hint">已创建。取消定时设置仍会保留此对象。</p>
      <fieldset v-if="!lockedObject" class="plan-object" :disabled="loading || busy">
        <legend>运行对象</legend>
        <label><input v-model="kind" type="radio" value="task" />单个任务</label>
        <label><input v-model="kind" type="radio" value="workflow" />整个工作流</label>
      </fieldset>
      <p v-if="loading" role="status">正在读取任务和工作流…</p>
      <p v-if="lockedObject" class="plan-hint">{{ kind === 'workflow' ? '工作流' : '单任务' }}：{{ selected?.name || '正在读取已创建对象…' }}</p>
      <label v-else class="field"><span>{{ kind === 'workflow' ? '选择工作流' : '选择任务' }}</span>
        <select v-model="selectedId" :disabled="loading || busy" required><option value="">请选择</option><option v-for="item in objects" :key="item.id" :value="String(item.id)">{{ item.name }}{{ kind === 'task' && !item.enabled ? '（已停用）' : '' }}</option></select>
      </label>
      <template v-for="(failure, type) in optionErrors" :key="type"><p v-if="failure" class="plan-error" role="alert">{{ type === 'workflow' ? '工作流' : '单任务' }}选项未完整载入：{{ failure }}</p></template>
      <p v-if="selected && kind === 'workflow'" class="plan-hint">{{ selected.steps.map(step => step.task_name).join(' → ') }}</p>
      <p v-if="selected && kind === 'workflow'" class="plan-hint">整个工作流共用此计划，成员需启用并设为手动。</p>
      <PlanFields :key="kind + ':' + selectedId" :form="form" :remote="kind === 'task' && Boolean(selected?.target_host_id)" :disabled="busy || loading || !selected" :identity="kind + ':' + selectedId" @unauthorized="handleSessionExpired" />
      <div v-if="error" class="plan-error" role="alert">{{ displayedError }}<button v-if="!busy" class="text-button" type="button" @click="refreshOptions">刷新选项</button></div>
    </form>
    <template #footer><button class="button secondary" type="button" :disabled="busy" @click="emit('close')">取消</button><button class="button primary" type="submit" form="unified-plan-form" :disabled="busy || loading || !selected || Boolean(optionErrors[kind])">{{ busy ? '正在保存…' : '保存定时' }}</button></template>
  </WorkflowDialog>
</template>

<style scoped>
.plan-form { display: grid; min-width: 0; grid-template-columns: minmax(0, 1fr); gap: 16px; }
.plan-form .plan-object { margin: 0; padding: 0; border: 0; border-radius: 0; display: flex; flex-wrap: wrap; gap: 12px 20px; min-width: 0; }
.plan-form legend { padding: 0 0 8px; font-size: 12px; }
.plan-form .field { display: grid; align-items: normal; gap: 6px; }
.plan-object label { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.plan-form input[type=checkbox], .plan-form input[type=radio] { width: 16px; height: 16px; }
.plan-form select, .plan-form input[type=time] { min-width: 0; max-width: 100%; }
.plan-hint { margin: 0; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.plan-error { padding: 12px; border: 1px solid #f0c2b7; border-radius: 8px; color: #ad4935; background: var(--coral-soft); font-size: 12px; overflow-wrap: anywhere; }
</style>
