<script setup>
import { ref, watch } from 'vue'
import { useWorkspaceContext } from '../../workspace/context'
import AiSettings from '../../AiSettings.vue'

const emit = defineEmits(['session-expired'])
const { isSuperAdmin, me } = useWorkspaceContext()
const instanceGeneration = ref(0)
watch(me, (next, previous) => {
  if (next !== previous) instanceGeneration.value += 1
}, { flush: 'sync' })
</script>

<template>
  <section class="view-stack">
    <AiSettings :key="instanceGeneration" :can-edit="isSuperAdmin" @session-expired="emit('session-expired')" />
  </section>
</template>
