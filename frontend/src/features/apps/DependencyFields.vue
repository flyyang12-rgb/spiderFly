<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { request } from '../../lib/api'
const props = defineProps({ script: Object, manualInput: { type: Boolean, default: true }, requirements: { type: String, default: '' },
  projectEntry: { type: String, default: '' }, target: [String, Number], taskId: Number,
  modelValue: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['update:modelValue', 'update:requirements', 'state', 'unauthorized'])
const result = ref(null), error = ref(''), status = ref('idle')
let sequence = 0, controller, timer, alive = true
function invalidate() {
  sequence++; clearTimeout(timer); controller?.abort(); result.value = null; error.value = ''
  status.value = props.script ? 'loading' : 'idle'
  emit('state', { status: status.value, ready: false })
}
async function analyze() {
  if (!props.script || !alive || (/\.zip$/i.test(props.script.name) && !props.projectEntry)) return
  const own = sequence
  controller = new AbortController()
  const form = new FormData(), project = /\.zip$/i.test(props.script.name)
  form.append(project ? 'project_archive' : 'script', props.script)
  if (project) form.append('project_entry', props.projectEntry)
  form.append('requirements_text', props.requirements)
  form.append('dependency_decisions', JSON.stringify(props.modelValue))
  if (props.taskId) form.append('task_id', String(props.taskId))
  else if (props.target) form.append('target_host_id', String(props.target))
  try {
    const value = await request(project ? '/apps/analyze-project-dependencies' : '/apps/analyze-dependencies', { method: 'POST', body: form, signal: controller.signal })
    if (!alive || own !== sequence) return
    if (!Array.isArray(value.items) || typeof value.requirements !== 'string' || typeof value.ready !== 'boolean') throw new Error('依赖识别未完成，请重试')
    result.value = value; status.value = value.ready ? 'ready' : 'error'
    if (!value.ready) error.value = '项目缺少模块：' + value.items.filter(item => value.unresolved.includes(item.id)).map(item => item.module).join('、')
    emit('state', { status: status.value, ready: value.ready, result: value })
  } catch (cause) {
    if (!alive || own !== sequence || cause.name === 'AbortError') return
    error.value = cause.message || '依赖识别失败'; status.value = 'error'
    emit('state', { status: 'error', ready: false })
    if (cause.status === 401) emit('unauthorized')
  }
}
watch(() => [props.script, props.projectEntry, props.target, props.taskId], () => {
  if (props.modelValue.length) emit('update:modelValue', [])
}, { flush: 'sync' })
watch(() => [props.script, props.projectEntry, props.requirements, props.target, props.taskId, JSON.stringify(props.modelValue)], () => {
  invalidate(); if (props.script) timer = setTimeout(analyze, 300)
}, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { alive = false; sequence++; controller?.abort(); clearTimeout(timer) })
</script>
<template>
  <section v-if="script" class="dependency-fields" aria-label="Python 依赖">
    <p v-if="status === 'loading'" class="dependency-status" role="status">正在识别依赖…</p>
    <p v-else-if="result?.ready" class="dependency-status" role="status">运行前自动准备独立环境<span v-if="result.requirements.trim()"> · {{ result.requirements.split('\n').filter(Boolean).length }} 项依赖</span></p>
    <p v-if="error" class="dependency-error" role="alert">{{ error }} <button type="button" class="text-button" :disabled="disabled" @click="invalidate(); analyze()">重试</button></p>
    <details v-if="result?.ready && result.requirements.trim()">
      <summary>依赖详情</summary>
      <pre>{{ result.requirements }}</pre>
    </details>
  </section>
</template>
<style scoped>
.dependency-fields{display:grid;gap:8px;min-width:0;font-size:13px}
.dependency-status{margin:0;padding:10px 12px;border-radius:8px;background:var(--surface-subtle);color:var(--muted);font-size:13px;line-height:1.6}
.dependency-fields summary{width:fit-content;color:var(--muted);font-size:13px;cursor:pointer}
.dependency-fields pre{margin:8px 0 0;padding:12px;border:1px solid var(--border);border-radius:8px;background:#fff;color:var(--text);font-size:13px;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere}
.dependency-error{margin:0;color:#ad4935;font-size:13px;line-height:1.6;overflow-wrap:anywhere}
</style>
