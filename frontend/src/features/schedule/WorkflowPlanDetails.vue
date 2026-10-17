<script setup>
import { formatTime } from '../../lib/format'
defineProps({ entry: { type: Object, required: true } })
defineEmits(['run'])
</script>
<template><div class="workflow-plan-details"><p v-if="entry.reason">{{ entry.reason }}</p><p v-if="entry.merged_from">较早漏点合并，仅处理最近点：{{ formatTime(entry.merged_from) }} 至 {{ formatTime(entry.merged_to) }}</p><button v-if="entry.run_id" class="text-button" type="button" @click.stop="$emit('run', entry.run_id)">查看流程运行 #{{ entry.run_id }}</button></div></template>
<style scoped>.workflow-plan-details { display: grid; grid-column: 1 / -1; gap: 4px; min-width: 0; }.workflow-plan-details p { margin: 0; color: var(--muted); font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }.workflow-plan-details button { justify-self: start; padding: 2px 0; white-space: normal; text-align: left; }</style>
