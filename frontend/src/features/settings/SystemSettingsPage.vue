<script setup>
import { computed } from 'vue'
import { useWorkspaceContext } from '../../workspace/context'
const { settings } = useWorkspaceContext()
const timeout = computed(() => {
  const value = Number(settings.value?.task_timeout_seconds)
  return value > 0 ? value % 60 === 0 ? `${value / 60} 分钟` : `${value} 秒` : '—'
})
</script>
<template>
  <details class="runtime-settings panel"><summary>运行信息</summary><dl><div><dt>执行并发</dt><dd>{{ settings.concurrency || '—' }}</dd></div><div><dt>调度时区</dt><dd>{{ settings.scheduler_timezone || '—' }}</dd></div><div><dt>任务超时</dt><dd>{{ timeout }}</dd></div><div><dt>公共文件夹</dt><dd>{{ settings.work_directory_name || '—' }}</dd></div><div><dt>浏览器端口</dt><dd>{{ settings.managed_browser_port || '—' }}</dd></div><div><dt>调度器</dt><dd>{{ settings.scheduler === 'running' ? '运行中' : settings.scheduler === 'stopped' ? '已停止' : '—' }}</dd></div></dl></details>
</template>
<style scoped>
.runtime-settings{min-width:0}.runtime-settings summary{cursor:pointer;padding:20px 24px;font-size:14px;font-weight:500;line-height:20px;list-style-position:inside}.runtime-settings summary:focus-visible{outline:2px solid #009139;outline-offset:2px}.runtime-settings dl{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px;margin:0;padding:4px 24px 24px}.runtime-settings dl>div{display:grid;gap:6px;min-width:0;font-size:13px;line-height:20px}.runtime-settings dt{color:var(--muted,#757a75)}.runtime-settings dd{margin:0;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}@media(max-width:720px){.runtime-settings dl{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:480px){.runtime-settings summary{padding:18px}.runtime-settings dl{padding:0 18px 18px;gap:20px}}
</style>
