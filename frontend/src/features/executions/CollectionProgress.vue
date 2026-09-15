<script setup>
import { computed } from 'vue'

const props = defineProps({ progress: Object, status: String })
const latest = computed(() => props.progress?.latest)
const finished = computed(() => !['pending', 'running'].includes(props.status))
const completeness = computed(() => {
  if (!finished.value) return '采集中，完整性待确认'
  if (props.status !== 'success') return '运行未成功，数据可能不完整'
  if (latest.value?.missing > 0 || latest.value?.failed_pages > 0 ||
      (latest.value?.total != null && latest.value?.collected != null && latest.value.collected < latest.value.total)) {
    return '数据有缺失'
  }
  return { complete: '脚本报告：采集完整', partial: '脚本报告：部分数据', unknown: '完整性未确认' }[latest.value?.completeness] || '完整性未确认'
})
const events = computed(() => [...(props.progress?.events || [])].reverse())
function eventLabel(event) {
  return event.stage || { progress: '采集进度', error: '采集出错', summary: '采集汇总' }[event.event]
}
function eventTime(value) {
  return new Date(value).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
}
</script>

<template>
  <section v-if="latest" class="collection-progress" aria-label="采集进度与结果">
    <div class="collection-heading">
      <strong>{{ finished ? '采集结果' : '采集进度' }}</strong>
      <span>{{ completeness }}</span>
    </div>
    <div class="collection-counts" aria-live="polite" aria-atomic="true">
      <div><small>已采集</small><strong>{{ latest.collected ?? '—' }} <small>条</small></strong></div>
      <div><small>目标总量</small><strong>{{ latest.total == null ? '未知' : `${latest.total} 条` }}</strong></div>
      <div v-if="latest.page != null"><small>最近上报页</small><strong>第 {{ latest.page }} 页</strong></div>
      <div v-if="latest.failed_pages != null"><small>失败页数</small><strong>{{ latest.failed_pages }}</strong></div>
      <div v-if="finished"><small>缺失条数</small><strong>{{ latest.missing ?? '未确认' }}</strong></div>
    </div>
    <p class="collection-stage">{{ latest.stage || (finished ? '最后上报' : '正在采集') }}<span v-if="latest.message"> · {{ latest.message }}</span></p>
    <small class="collection-updated">最后上报 {{ eventTime(latest.at) }}（北京时间）；数量由脚本上报，最终以结果文件和验收为准。</small>
    <details class="collection-events">
      <summary>查看采集过程（{{ events.length }} 条{{ progress.event_count > events.length ? '，仅显示最近记录' : '' }}）</summary>
      <ol>
        <li v-for="(event, index) in events" :key="index" :class="{ 'collection-error': event.event === 'error' }">
          <time>{{ eventTime(event.at) }}</time>
          <div><strong>{{ eventLabel(event) }}</strong><span v-if="event.page != null"> · 第 {{ event.page }} 页</span><span v-if="event.collected != null"> · 已采集 {{ event.collected }} 条</span><p v-if="event.message">{{ event.message }}</p></div>
        </li>
      </ol>
    </details>
  </section>
  <p v-else class="artifact-empty">{{ status === 'pending' ? '任务尚未开始' : '脚本尚未上报采集进度，可查看下方原始日志。' }}</p>
</template>

<style scoped>
.collection-progress { border: 1px solid var(--border); border-radius: 12px; padding: 16px; background: var(--surface); }
.collection-heading { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.collection-heading > span, .collection-updated { color: var(--muted); font-size: 12px; }
.collection-counts { display: flex; flex-wrap: wrap; gap: 20px; margin: 16px 0; }
.collection-counts > div { display: grid; gap: 5px; min-width: 85px; }
.collection-counts small { color: var(--muted); font-size: 12px; font-weight: normal; }
.collection-counts strong { font-size: 19px; }
.collection-stage { margin: 8px 0; overflow-wrap: anywhere; }
.collection-updated { display: block; }
.collection-events { margin-top: 14px; border-top: 1px solid var(--border-soft); padding-top: 12px; }
.collection-events summary { cursor: pointer; font-size: 13px; }
.collection-events ol { max-height: 280px; overflow-y: auto; padding: 0; list-style: none; }
.collection-events li { display: flex; gap: 12px; padding: 10px 0; font-size: 13px; border-bottom: 1px solid var(--border-soft); }
.collection-events time { color: var(--muted); flex-shrink: 0; }
.collection-events li > div { min-width: 0; overflow-wrap: anywhere; }
.collection-events p { margin: 4px 0 0; white-space: pre-wrap; }
.collection-error { color: #b43d2a; }
</style>
