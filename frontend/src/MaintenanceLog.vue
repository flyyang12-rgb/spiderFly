<script setup>
import { computed } from 'vue'
import { maintenanceSummary } from './maintenanceSummary'
const props = defineProps({ job: { type: Object, required: true }, executionId: Number, compact: Boolean })
const emit = defineEmits(['open-execution'])
const summary = computed(() => maintenanceSummary(props.job))
const events = computed(() => props.job.events || [])
</script>

<template>
  <div class="maintenance-log" :class="{ 'maintenance-compact': compact }">
    <p v-if="!compact" class="repair-summary" :class="{ 'repair-failed': ['failed', 'timeout', 'cancelled'].includes(job.rerun_status) }">{{ summary }}</p>
    <button v-if="job.rerun_execution_id && job.rerun_execution_id !== executionId" class="button ghost" @click="emit('open-execution', job.rerun_execution_id)">查看重跑日志</button>
    <details v-if="events.length || job.calls != null || (job.note && (compact || job.note !== summary))" class="repair-technical">
      <summary>{{ compact ? '修复详情' : '技术详情' }}</summary>
      <p v-if="compact">{{ summary }}</p>
      <small v-if="job.calls != null">{{ job.calls }} 次调用 · 已确认 {{ ((job.input_tokens || 0) + (job.output_tokens || 0)).toLocaleString() }} tokens · {{ Math.ceil(job.elapsed_seconds || 0) }} 秒</small>
      <ol v-if="events.length"><li v-for="(event, index) in events" :key="index">{{ event.message }}</li></ol>
      <p v-else-if="!compact">{{ job.note }}</p>
    </details>
  </div>
</template>

<style scoped>
.maintenance-compact{display:flex;align-items:baseline;gap:4px 16px;flex-wrap:wrap}
.maintenance-compact>.button{padding:0;min-height:0;font-size:12px}
.maintenance-compact>.repair-technical{margin:0}
.maintenance-compact>.repair-technical[open]{flex-basis:100%;margin-top:8px}
.repair-summary.repair-failed{color:#a64736}
.repair-summary{margin:10px 0;font-size:14px;line-height:1.8;color:#385b48;white-space:pre-wrap;overflow-wrap:anywhere}.repair-technical{margin:8px 0 12px;font-size:12px;color:#74877a}.repair-technical>summary{cursor:pointer;width:fit-content}.repair-technical small{display:block;margin:10px 0}.repair-technical ol{padding-left:20px;line-height:1.9}.repair-technical li,.repair-technical p{white-space:pre-wrap;overflow-wrap:anywhere}
</style>
