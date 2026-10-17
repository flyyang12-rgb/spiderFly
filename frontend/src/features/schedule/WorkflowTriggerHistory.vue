<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { request } from '../../lib/api'
import { formatTime } from '../../lib/format'
import { workflowStatusLabel } from '../workflows/useWorkflows'
import { useWorkspaceContext } from '../../workspace/context'
import WorkflowDialog from '../workflows/WorkflowDialog.vue'
const props = defineProps({ workflow: { type: Object, required: true } })
const emit = defineEmits(['close', 'run'])
const { handleSessionExpired } = useWorkspaceContext()
const records = ref([])
const next = ref(null)
const loading = ref(false)
const error = ref('')
const controller = new AbortController()
let disposed = false
async function load(more = false) {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const query = more && next.value ? '?before_id=' + next.value : ''
    const result = await request(`/workflows/${props.workflow.id}/triggers${query}`, { signal: controller.signal })
    if (disposed) return
    records.value = more ? records.value.concat(result.items) : result.items
    next.value = result.next_before_id
  } catch (failure) { if (!disposed && failure.name !== 'AbortError') { error.value = failure.message; if (failure.status === 401) handleSessionExpired() } }
  finally { if (!disposed) loading.value = false }
}
const label = state => ({ error: '触发失败', registered: '等待登记', active: '运行中', scheduled: '待执行' })[state] || workflowStatusLabel(state)
onMounted(() => load())
onBeforeUnmount(() => { disposed = true; controller.abort() })
</script>
<template>
  <WorkflowDialog :title="workflow.name + ' · 触发记录'" @close="emit('close')">
    <div v-if="error" class="trigger-error" role="alert">{{ error }}<button class="text-button" @click="load()">重试</button></div>
    <ol v-if="records.length" class="trigger-records"><li v-for="record in records" :key="record.trigger_id"><div><strong>计划 {{ formatTime(record.plannedAt) }}</strong><span>{{ label(record.state) }}</span></div><p v-if="record.reason">{{ record.reason }}</p><p v-if="record.merged_from">较早漏点合并：{{ formatTime(record.merged_from) }} 至 {{ formatTime(record.merged_to) }}，仅处理最近点。</p><button v-if="record.run_id" class="text-button" @click="emit('run', record.run_id)">查看流程运行 #{{ record.run_id }}</button></li></ol>
    <p v-else>{{ loading ? '正在读取…' : '尚无计划触发记录' }}</p>
    <button v-if="next" class="button secondary" :disabled="loading" @click="load(true)">{{ loading ? '正在读取…' : '加载更早记录' }}</button>
  </WorkflowDialog>
</template>
<style scoped>
.trigger-records { display: grid; gap: 0; padding: 0; margin: 0; list-style: none; }
.trigger-records li { padding: 16px 0; border: 0; border-bottom: 1px solid var(--border-soft); border-radius: 0; min-width: 0; }
.trigger-records li > div { display: flex; flex-wrap: wrap; gap: 8px 16px; justify-content: space-between; font-size: 12px; }
.trigger-records p, .trigger-error { font-size: 12px; overflow-wrap: anywhere; white-space: pre-wrap; }
.trigger-error { color: #ad4935; }
</style>
