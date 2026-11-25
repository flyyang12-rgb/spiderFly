<script setup>
import { ref, watch } from 'vue'
import { useWorkspaceContext } from '../../workspace/context'
import AiSettings from '../../AiSettings.vue'
import FeishuSettings from './FeishuSettings.vue'
import MembersPage from '../members/MembersPage.vue'
const emit = defineEmits(['session-expired'])
const { isSuperAdmin, me } = useWorkspaceContext()
const instanceGeneration = ref(0)
watch(me, (next, previous) => { if (next !== previous) instanceGeneration.value += 1 }, { flush: 'sync' })
</script>
<template>
  <section class="view-stack service-settings-page">
    <MembersPage />
    <div class="service-settings-grid">
      <AiSettings :key="'ai-' + instanceGeneration" :can-edit="isSuperAdmin" @session-expired="emit('session-expired')" />
      <FeishuSettings :key="'feishu-' + instanceGeneration" :can-edit="isSuperAdmin" @session-expired="emit('session-expired')" />
    </div>
  </section>
</template>
<style scoped>
.service-settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 20px;
}
.service-settings-grid > .panel {
  display: grid;
  min-width: 0;
  grid-row: span 2;
  grid-template-rows: subgrid;
}
@media (max-width: 920px) {
  .service-settings-grid { grid-template-columns: minmax(0, 1fr); gap: 16px; }
  .service-settings-grid > .panel { display: block; grid-row: auto; }
}
</style>
