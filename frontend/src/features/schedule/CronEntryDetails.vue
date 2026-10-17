<script setup>
import { computed } from 'vue'
import { formatTime, statusLabel } from '../../lib/format'
import { useWorkspaceContext } from '../../workspace/context'

const props = defineProps({ entry: { type: Object, required: true } })
const { openExecution } = useWorkspaceContext()
const maintenanceLabel = computed(() => ({
  pending: '等待生成修复',
  generating: '正在生成修复',
  ready: '等待验证',
  testing: '正在验证',
  activated: '修复已启用',
  budget: '维护预算不足',
  review: '维护完成，保留原版本',
  expired: '维护已过期',
  failed: '维护失败',
  cancelled: '维护已取消',
  interrupted: '维护已中断',
  exhausted: '维护预算已用完',
  unsupported: '无法自动维护',
  rejected: '未启用修复',
  stale: '维护条件已变化',
})[props.entry.maintenance_status] || (props.entry.maintenance_status ? '维护状态待确认' : '维护记录暂不可用'))
</script>

<template>
  <div class="cron-entry-details">
    <p v-if="entry.reason">{{ entry.reason }}</p>
    <p v-if="entry.maintenance_job_id">自动维护 · {{ maintenanceLabel }}</p>
    <p v-if="entry.maintenance_reason">{{ entry.maintenance_decision === 'none' ? '未进入维护' : '维护决定' }}：{{ entry.maintenance_reason }}</p>
    <p v-if="entry.merged_from">合并范围：{{ formatTime(entry.merged_from) }} 至 {{ formatTime(entry.plannedAt) }}</p>
    <div v-if="entry.execution || entry.rerun_execution" class="cron-entry-links">
      <button v-if="entry.execution" class="text-button" type="button"
        @click.stop="openExecution(entry.execution)" @keydown.enter.stop>
        原运行 · {{ statusLabel(entry.execution.status) }}
      </button>
      <button v-if="entry.rerun_execution" class="text-button" type="button"
        @click.stop="openExecution(entry.rerun_execution)" @keydown.enter.stop>
        修复重跑 · {{ statusLabel(entry.rerun_execution.status) }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.cron-entry-details { display: grid; gap: 4px; width: 100%; min-width: 0; }
.cron-entry-details p { margin: 0; color: var(--muted); font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }
.cron-entry-links { display: flex; flex-wrap: wrap; gap: 4px 10px; }
.cron-entry-links .text-button { padding: 2px 0; text-align: left; white-space: normal; }
</style>
