<script setup>
import { computed, nextTick, provide, ref } from 'vue'
import { useWorkspaceContext } from '../../workspace/context'
import ExecutionHistory from './ExecutionHistory.vue'
import WorkflowHistory from '../workflows/WorkflowHistory.vue'

const workspace = useWorkspaceContext()
const kind = workspace.runHistoryKind || ref('task')
const historyComponent = computed(() => kind.value === 'workflow' ? WorkflowHistory : ExecutionHistory)
const page = ref(null)
provide('spiderfly-history-filters-expanded', ref(false))
async function restoreTypeFocus() {
  await nextTick()
  page.value?.querySelector('[data-record-type]')?.focus()
}
</script>

<template>
  <section ref="page" class="view-stack run-history-page">
    <KeepAlive>
      <component :is="historyComponent">
        <template #scope>
          <label class="filter-field"><span>记录类型</span>
            <select v-model="kind" data-record-type @change="restoreTypeFocus"><option value="task">任务执行</option><option value="workflow">流程整轮</option></select>
          </label>
        </template>
        <template #scope-help>
          <details class="run-history-help"><summary>记录范围</summary><p>任务执行含工作流步骤；流程整轮查看整轮与步骤。远程任务记录位于“宿主机”。</p></details>
        </template>
      </component>
    </KeepAlive>
  </section>
</template>

<style scoped>
.run-history-page { min-width: 0; grid-template-columns: minmax(0, 1fr); }
.run-history-help { min-width: 0; color: var(--muted); font-size: 12px; }
.run-history-help summary { cursor: pointer; }
.run-history-help p { margin: 8px 0 0; max-width: 440px; color: var(--muted); line-height: 20px; overflow-wrap: anywhere; }
</style>
