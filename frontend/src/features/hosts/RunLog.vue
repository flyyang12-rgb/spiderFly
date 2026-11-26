<script setup>
import { computed } from 'vue'
import { runStatusLabels } from './useHosts'

const props = defineProps({ events: { type: Array, default: () => [] } })
const blocks = computed(() => {
  const result = []
  for (const event of props.events) {
    const text = event.text || ({
      started: '开始执行', finished: runStatusLabels[event.status] || '运行结束', uncertain: '状态待确认',
    }[event.kind] || '')
    const previous = result.at(-1)
    const stream = event.kind === 'stdout' || event.kind === 'stderr'
    if (stream && previous?.kind === event.kind && previous.end + 1 === event.seq) {
      previous.text += text
      previous.end = event.seq
    } else {
      result.push({ seq: event.seq, end: event.seq, kind: event.kind, text })
    }
  }
  return result.filter(block => block.text.trim())
})
</script>

<template>
  <div v-if="blocks.length" class="run-log" tabindex="0" role="region" aria-label="日志内容">
    <pre v-for="block in blocks" :key="block.seq" :class="{ 'log-error': block.kind === 'stderr' }" :aria-label="block.kind === 'stderr' ? '错误输出' : undefined">{{ block.text }}</pre>
  </div>
  <p v-else class="log-empty">暂无日志</p>
</template>

<style scoped>
.run-log { max-height: max(280px, calc(100dvh - 300px)); overflow: auto; padding: 14px 16px; border: 1px solid var(--host-border); border-radius: 8px; background: var(--host-soft); overscroll-behavior: contain; }
.run-log pre { margin: 0; padding: 0; max-height: none; border: 0; border-radius: 0; background: transparent; color: var(--host-text); font: var(--log-font-size)/var(--log-line-height) Consolas, monospace; white-space: pre-wrap; overflow: visible; overflow-wrap: anywhere; }
.run-log .log-error { color: #a53721; }
.run-log:focus-visible { outline: 2px solid var(--control-focus); outline-offset: 2px; }
.log-empty { color: var(--host-muted); font-size: var(--workspace-caption); }
</style>
