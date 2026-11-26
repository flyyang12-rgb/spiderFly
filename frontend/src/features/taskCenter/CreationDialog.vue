<script setup>
import { computed, onBeforeUnmount, watch } from 'vue'
import { useWorkspaceContext } from '../../workspace/context'
import CreateTaskPage from '../apps/CreateTaskPage.vue'
import WorkflowDialog from '../workflows/WorkflowDialog.vue'

const emit = defineEmits(['created'])
const { me, isAdmin, appForm, createObjectOpen, uploadApp, uploading, uploadUncertain,
  requirementsReading, resetCreateSession, resolveUpload } = useWorkspaceContext()
const saveDisabled = computed(() => uploading.value || uploadUncertain.value || requirementsReading.value
  || appForm.project_loading || Boolean(appForm.dependency_status && !['ready', 'idle'].includes(appForm.dependency_status)))
let disposed = false

function close() {
  if (!uploading.value) createObjectOpen.value = false
}

async function finish(request) {
  if (!isAdmin.value || uploading.value || !me.value) return
  const owner = me.value
  const result = await request()
  if (!result || disposed || me.value !== owner) return
  emit('created', result)
  createObjectOpen.value = false
}

watch(me, () => { createObjectOpen.value = false }, { flush: 'sync' })
onBeforeUnmount(() => {
  disposed = true
  resetCreateSession()
})
</script>

<template>
  <WorkflowDialog title="新建任务" wide :close-disabled="uploading" @close="close">
    <CreateTaskPage embedded form-id="unified-create-task" :disabled="uploading" @submit="finish(uploadApp)" />
    <div v-if="uploadUncertain" class="creation-recovery">
      <button type="button" class="button secondary" :disabled="uploading" @click="finish(resolveUpload)">核对创建结果</button>
      <button type="button" class="text-button" :disabled="uploading" @click="finish(() => uploadApp({ retryUncertain: true }))">原请求重试</button>
    </div>
    <template #footer>
      <button class="button secondary" type="button" :disabled="uploading" @click="close">取消</button>
      <button class="button primary" type="submit" form="unified-create-task" :disabled="saveDisabled">{{ uploading ? '正在保存…' : '保存' }}</button>
    </template>
  </WorkflowDialog>
</template>

<style scoped>
.creation-recovery { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 12px; }
</style>
