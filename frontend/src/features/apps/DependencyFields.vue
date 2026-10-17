<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { request } from '../../lib/api'
const props = defineProps({ script: Object, requirements: { type: String, default: '' },
  target: [String, Number], taskId: Number, modelValue: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['update:modelValue', 'update:requirements', 'state', 'unauthorized'])
const result = ref(null), lastItems = ref([]), error = ref(''), status = ref('idle'), reading = ref(false)
const pending = computed(() => lastItems.value.filter(item => !['stdlib', 'type_only', 'import'].includes(item.kind)))
let sequence = 0, fileSequence = 0, controller, timer, alive = true
function invalidate() {
  sequence++; clearTimeout(timer); controller?.abort(); result.value = null; error.value = ''
  status.value = props.script ? 'loading' : 'idle'; emit('state', { status: status.value, ready: false })
}
async function analyze() {
  if (!props.script || !alive) return
  if (props.modelValue.some(item => item.action === 'package' ? !item.requirement?.trim() : !item.reason?.trim())) {
    status.value = 'unresolved'; emit('state', { status: 'unresolved', ready: false }); return
  }
  const own = sequence
  controller = new AbortController()
  const form = new FormData()
  form.append('script', props.script); form.append('requirements_text', props.requirements)
  form.append('dependency_decisions', JSON.stringify(props.modelValue))
  if (props.taskId) form.append('task_id', String(props.taskId))
  else if (props.target) form.append('target_host_id', String(props.target))
  try {
    const value = await request('/apps/analyze-dependencies', { method: 'POST', body: form, signal: controller.signal })
    if (!alive || own !== sequence) return
    if (!Array.isArray(value.items) || !Array.isArray(value.unresolved) || typeof value.requirements !== 'string' || typeof value.ready !== 'boolean') throw new Error('依赖分析响应不完整，请重试')
    result.value = value; lastItems.value = value.items; status.value = value.ready ? 'ready' : 'unresolved'
    emit('state', { status: status.value, ready: Boolean(value.ready), result: value })
  } catch (e) {
    if (!alive || own !== sequence || e.name === 'AbortError') return
    error.value = e.message || '依赖分析失败'; status.value = 'error'
    emit('state', { status: 'error', ready: false }); if (e.status === 401) emit('unauthorized')
  }
}
watch(() => [props.script, props.target, props.taskId], () => {
  lastItems.value = []
  if (props.modelValue.length) emit('update:modelValue', [])
}, { flush: 'sync' })
watch(() => [props.script, props.requirements, props.target, props.taskId, JSON.stringify(props.modelValue)], () => {
  invalidate(); if (props.script) timer = setTimeout(analyze, 300)
}, { immediate: true, flush: 'sync' })
function choice(id) { return props.modelValue.find(item => item.id === id) || { id, action: '', requirement: '', reason: '' } }
function update(id, key, value) {
  const next = { ...choice(id), [key]: value }
  if (key === 'action' && value === 'package' && !next.requirement) next.requirement = pending.value.find(item => item.id === id)?.package || ''
  emit('update:modelValue', [...props.modelValue.filter(item => item.id !== id), next].filter(item => item.action))
}
async function readFile(event) {
  const file = event.target.files?.[0], fileOwn = ++fileSequence
  if (!file) return
  invalidate()
  const own = sequence
  reading.value = true
  try {
    if (!file.name.toLowerCase().endsWith('.txt') || file.size > 100 * 1024) throw new Error('请选择不超过 100KB 的 UTF-8 依赖文本')
    const value = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer()).replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').trim()
    if (!value || value.includes('\0') || value.length > 20000) throw new Error('依赖文本不能为空且不能超过 20,000 字符')
    if (alive && own === sequence && fileOwn === fileSequence) {
      const unchanged = value === props.requirements
      emit('update:requirements', value)
      if (unchanged) timer = setTimeout(analyze, 0)
    }
  } catch (e) { if (alive && own === sequence && fileOwn === fileSequence) {
    error.value = e.message || '依赖文件读取失败'; status.value = 'error'; emit('state', { status: 'error', ready: false })
  } }
  finally { if (alive && fileOwn === fileSequence) reading.value = false; event.target.value = '' }
}
onBeforeUnmount(() => { alive = false; sequence++; fileSequence++; controller?.abort(); clearTimeout(timer) })
</script>
<template>
  <section class="dependency-fields" aria-label="Python 依赖">
    <p v-if="status === 'loading'" role="status">正在识别依赖…</p>
    <p v-else-if="result" role="status">{{ result.ready ? `已识别 ${result.requirements.split('\n').filter(Boolean).length} 项依赖` : `${result.unresolved.length} 项依赖待补充` }}</p>
    <div v-if="error" class="dependency-error" role="alert">{{ error }} <button type="button" class="text-button" :disabled="disabled" @click="invalidate(); analyze()">重新分析</button></div>
    <div v-if="pending.length" class="dependency-pending">
      <article v-for="item in pending" :key="item.id">
        <strong>{{ item.module }} <small>第 {{ item.lines.join('、') }} 行</small></strong>
        <p>{{ item.reason || '条件导入，需按实际用途选择' }}</p>
        <label class="field"><span class="sr-only">{{ item.module }} 处理方式</span><select :value="choice(item.id).action" :disabled="disabled" @change="update(item.id, 'action', $event.target.value)">
          <option value="">选择处理方式</option><option value="package">安装指定包</option><option value="exclude">不安装，说明理由</option>
        </select></label>
        <label v-if="choice(item.id).action === 'package'" class="field"><span>包名或版本约束</span><input :value="choice(item.id).requirement" :placeholder="item.package || '例如：requests>=2.31'" :disabled="disabled" @input="update(item.id, 'requirement', $event.target.value)"></label>
        <label v-if="choice(item.id).action === 'exclude'" class="field"><span>排除理由</span><input :value="choice(item.id).reason" maxlength="500" placeholder="例如：仅用于不执行的可选分支" :disabled="disabled" @input="update(item.id, 'reason', $event.target.value)"></label>
      </article>
      <small>单文件上传不包含其他业务模块；排除后需自行准备相应文件。</small>
    </div>
    <details v-if="result"><summary>识别明细</summary><ul><li v-for="item in result.items" :key="item.id">{{ item.module }} · {{ item.kind === 'stdlib' ? '标准库' : item.kind === 'type_only' ? '仅类型检查' : item.requirement || item.package || item.exclusion_reason || '待补充' }} · 第 {{ item.lines.join('、') }} 行</li></ul><pre v-if="result.requirements">{{ result.requirements }}</pre><small>{{ result.target?.kind === 'agent' ? '目标 Python 待实际准备时核验' : `Python ${result.target?.version || '3.12'}` }}</small></details>
    <details><summary>补充依赖 <small>可选</small></summary>
      <label class="field"><span>requirements.txt</span><input type="file" accept=".txt,text/plain" :disabled="disabled || reading" @change="readFile"></label>
      <label class="field"><span>包名和版本约束</span><textarea :value="requirements" maxlength="20000" rows="3" :disabled="disabled || reading" placeholder="每行一个，例如：openpyxl>=3.1" @input="emit('update:requirements', $event.target.value)"></textarea></label>
    </details>
  </section>
</template>
<style scoped>
.dependency-fields{display:grid;gap:8px;min-width:0;font-size:13px}.dependency-fields p{margin:0}.dependency-fields summary{cursor:pointer;color:var(--muted);font-size:12px}.dependency-fields details[open]{display:grid;gap:8px}.dependency-fields details .field{margin-top:8px}.dependency-fields ul{padding-left:18px;font-size:12px;line-height:1.8}.dependency-fields pre{white-space:pre-wrap;overflow-wrap:anywhere;color:var(--text);background:var(--surface-subtle);border:1px solid var(--border-soft);padding:8px;border-radius:6px;font-size:12px;line-height:1.7}.dependency-fields small{color:var(--muted);font-weight:400;font-size:11px}.dependency-pending{display:grid;gap:8px}.dependency-pending article{display:grid;gap:8px;border:1px solid var(--border);padding:12px;border-radius:8px}.dependency-pending p,.dependency-error{font-size:12px;overflow-wrap:anywhere}.dependency-error{color:#ad4935}.dependency-pending strong{overflow-wrap:anywhere}.dependency-pending strong small{margin-left:8px}.dependency-fields input,.dependency-fields select,.dependency-fields textarea{max-width:100%;min-width:0;box-sizing:border-box}
</style>
