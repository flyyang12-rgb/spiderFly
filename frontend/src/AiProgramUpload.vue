<script setup>
import { onBeforeUnmount, ref } from 'vue'
const props = defineProps({ threadId: Number, program: Object, disabled: Boolean, readonly: Boolean })
const emit = defineEmits(['changed', 'busy', 'error'])
const input = ref(null)
const uploading = ref(false)
let alive = true
onBeforeUnmount(() => { alive = false })

function open() {
  if (!props.threadId || props.disabled || props.readonly || uploading.value) return
  input.value?.click()
}
defineExpose({ open, uploading })

async function change(event) {
  const file = event.target.files?.[0]
  if (!file || props.disabled || props.readonly || uploading.value) return
  const zip = /\.zip$/i.test(file.name)
  if ((!zip && !/\.py$/i.test(file.name)) || file.size > (zip ? 50 : 2) * 1024 * 1024) {
    emit('error', '请选择 2 MB 以内的 PY 或 50 MB 以内的 ZIP')
    event.target.value = ''
    return
  }
  const body = new FormData()
  body.append('program', file)
  await submit('POST', body)
  event.target.value = ''
}

async function submit(method, body) {
  if (!props.threadId || props.disabled || props.readonly || uploading.value) return
  const id = props.threadId
  uploading.value = true
  emit('busy', true)
  emit('error', '')
  try {
    const response = await fetch(`/api/ai/threads/${id}/program`, { method, credentials: 'include', body })
    const result = await response.json()
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '程序上传失败，请重试')
    if (alive && id === props.threadId) emit('changed')
  } catch (cause) { if (alive) emit('error', cause.message) }
  finally { if (alive) { uploading.value = false; emit('busy', false) } }
}
</script>

<template>
  <div class="ai-program-upload" :class="{ 'has-program': Boolean(program) }">
    <input ref="input" type="file" accept=".py,.zip" hidden aria-label="上传 PY 或 ZIP 程序" :disabled="!threadId || disabled || readonly || uploading" @change="change" />
    <div v-if="program" class="ai-program-file" aria-label="已上传程序">
      <span class="ai-program-kind">{{ program.kind === 'zip' ? 'ZIP' : 'PY' }}</span>
      <span class="ai-program-name" :title="program.filename">{{ program.filename }}</span>
      <button v-if="!readonly" type="button" class="ai-program-remove" aria-label="移除上传程序" :disabled="disabled || uploading" @click="submit('DELETE')">×</button>
    </div>
  </div>
</template>

<style scoped>
.ai-program-upload{display:flex;align-items:center;flex-wrap:wrap;gap:8px;min-width:0}
.ai-program-upload.has-program{margin-bottom:8px}
.ai-program-file{display:flex;align-items:center;gap:8px;min-width:0;max-width:100%;padding:6px 8px;border:1px solid var(--border);border-radius:8px;background:var(--surface-subtle);font-size:13px}
.ai-program-kind{flex:none;color:var(--brand-strong);font-size:11px}
.ai-program-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ai-program-remove{flex:none;display:grid;place-items:center;width:26px;height:26px;border:1px solid var(--border);border-radius:6px;background:#fff;color:var(--muted);font-size:18px;cursor:pointer}
.ai-program-remove:disabled{opacity:.5;cursor:default}
</style>
