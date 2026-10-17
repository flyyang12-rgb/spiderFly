<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { request } from '../../lib/api'
import { triggerConfig, formatTime } from '../../lib/format'
import { weekdayOptions } from '../../lib/constants'
import CronEditor from './CronEditor.vue'

const props = defineProps({ form: { type: Object, required: true }, disabled: Boolean, remote: Boolean, identity: { type: [String, Number], default: '' } })
const emit = defineEmits(['unauthorized'])
const scheduled = computed(() => props.form.trigger_type !== 'manual')
const lastType = ref(props.form.saved_trigger_type || (scheduled.value ? props.form.trigger_type : 'daily'))
const times = ref([]), error = ref(''), loading = ref(false)
let sequence = 0, timer, controller
function mode(enabled) { props.form.trigger_type = enabled ? lastType.value : 'manual' }
watch(() => props.form.trigger_type, type => { if (type !== 'manual') lastType.value = type })
watch(() => props.identity, () => { lastType.value = props.form.saved_trigger_type || 'daily' })
function invalidate() {
  sequence++; window.clearTimeout(timer); controller?.abort()
  times.value = []; error.value = ''; loading.value = false
}
watch(() => [props.identity, props.form.trigger_type, JSON.stringify(triggerConfig(props.form))], () => {
  invalidate()
  if (!['daily', 'weekly'].includes(props.form.trigger_type)) return
  const current = sequence
  const body = JSON.stringify({ trigger_type: props.form.trigger_type, trigger_config: triggerConfig(props.form) })
  loading.value = true
  timer = window.setTimeout(async () => {
    controller = new AbortController()
    try {
      const result = await request('/schedules/preview-plan', { method: 'POST', body, signal: controller.signal })
      if (current === sequence) times.value = result.times
    } catch (failure) {
      if (current !== sequence || failure.name === 'AbortError') return
      error.value = failure.message || '无法预览计划'
      if (failure.status === 401) emit('unauthorized')
    } finally { if (current === sequence) loading.value = false }
  }, 200)
}, { immediate: true })
onBeforeUnmount(invalidate)
</script>

<template>
  <fieldset class="plan-fields" :disabled="disabled">
    <legend>执行方式</legend>
    <div class="plan-mode"><button type="button" :class="{ active: !scheduled }" :aria-pressed="!scheduled" @click="mode(false)">手动</button><button type="button" :class="{ active: scheduled }" :aria-pressed="scheduled" @click="mode(true)">定时</button></div>
    <template v-if="scheduled">
      <label class="field"><span>定时方式</span><select v-model="form.trigger_type"><option value="daily">每日</option><option value="weekly">每周</option><option value="cron" :disabled="remote">Cron</option></select></label>
      <fieldset v-if="form.trigger_type === 'weekly'" class="plan-weekdays"><legend>执行星期</legend><label v-for="day in weekdayOptions" :key="day.value"><input v-model="form.weekly_days" type="checkbox" :value="day.value" />周{{ day.label }}</label></fieldset>
      <label v-if="form.trigger_type === 'daily'" class="field"><span>执行时间（北京时间）</span><input v-model="form.daily_time" type="time" required /></label>
      <label v-else-if="form.trigger_type === 'weekly'" class="field"><span>执行时间（北京时间）</span><input v-model="form.weekly_time" type="time" required /></label>
      <p v-if="remote" class="plan-help">此电脑支持每日、每周定时。</p>
      <p v-if="remote && form.trigger_type === 'cron'" class="danger-text" role="alert">Cron 仅支持主控本机，请修改运行电脑或定时方式。</p>
      <CronEditor v-if="form.trigger_type === 'cron'" v-model="form.cron_expression" :disabled="disabled" compact />
      <p v-if="error" class="danger-text plan-help" role="alert">{{ error }}</p>
      <details v-if="['daily', 'weekly'].includes(form.trigger_type)" class="plan-times"><summary>未来 5 次 · 北京时间</summary><p v-if="loading" role="status">正在计算…</p><ol v-else><li v-for="point in times" :key="point">{{ formatTime(point) }}</li></ol></details>
    </template>
  </fieldset>
</template>

<style scoped>
.plan-fields { display: grid; gap: 12px; min-width: 0; margin: 0; padding: 0; border: 0; }
.plan-fields legend { padding: 0 0 8px; font-size: 12px; }
.plan-mode { display: flex; gap: 8px; }
.plan-mode button { padding: 8px 16px; border: 1px solid var(--border); border-radius: 8px; color: var(--text); background: var(--surface); font: inherit; font-size: 13px; cursor: pointer; }
.plan-mode button.active { color: var(--brand-strong); border-color: var(--brand); background: var(--brand-soft); }
.plan-mode button:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.plan-weekdays { display: flex; flex-wrap: wrap; gap: 12px; border: 0; padding: 0; margin: 0; min-width: 0; }
.plan-weekdays label { display: flex; gap: 4px; align-items: center; font-size: 13px; }
.plan-fields select, .plan-fields input { min-width: 0; max-width: 100%; }
.plan-help, .plan-times { margin: 0; font-size: 12px; overflow-wrap: anywhere; }
.plan-help:not(.danger-text), .plan-times { color: var(--muted); }
.plan-times summary { cursor: pointer; }
.plan-times ol { margin: 8px 0 0; padding-left: 20px; line-height: 1.8; }
</style>
