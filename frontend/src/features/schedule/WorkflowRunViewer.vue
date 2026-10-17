<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { request } from '../../lib/api'
import { useWorkspaceContext } from '../../workspace/context'
import WorkflowRunDetail from '../workflows/WorkflowRunDetail.vue'
const props = defineProps({ runId: { type: Number, required: true } })
const emit = defineEmits(['close'])
const { openExecution, handleSessionExpired } = useWorkspaceContext()
const controller = new AbortController()
const state = { selectedRunId: ref(props.runId), runDetail: ref(null), detailLoading: ref(false), detailError: ref(''),
  workflows: ref([]), busy: ref(''), actionError: ref(''), closeRun: () => emit('close'), loadRunDetail: load }
let disposed = false
let timer
async function load() {
  if (disposed || state.detailLoading.value) return
  state.detailLoading.value = true
  try {
    const result = await request(`/workflow-runs/${props.runId}`, { signal: controller.signal })
    if (disposed) return
    state.runDetail.value = result
    state.detailError.value = ''
    if (['waiting','running','stopping'].includes(result.status)) timer = window.setTimeout(load, 1500)
  } catch (error) { if (!disposed && error.name !== 'AbortError') { state.detailError.value = error.message; if (error.status === 401) handleSessionExpired() } }
  finally { if (!disposed) state.detailLoading.value = false }
}
onMounted(load)
onBeforeUnmount(() => { disposed = true; controller.abort(); window.clearTimeout(timer) })
</script>
<template><WorkflowRunDetail :state="state" :can-manage="false" @execution="id => { emit('close'); openExecution({ id }) }" /></template>
