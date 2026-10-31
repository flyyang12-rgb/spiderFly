<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { request } from '../../lib/api'
import { triggerConfig, formatTime } from '../../lib/format'
import { weekdayOptions } from '../../lib/constants'

const props = defineProps({ form: { type: Object, required: true }, disabled: Boolean, remote: Boolean, identity: { type: [String, Number], default: '' } })
const emit = defineEmits(['unauthorized'])
const scheduled = computed(() => props.form.trigger_type !== 'manual')
const lastType = ref(scheduled.value ? props.form.trigger_type : props.form.saved_trigger_type || 'daily')
const times = ref([]), error = ref(''), loading = ref(false)
let sequence = 0, timer, controller
function mode(enabled) { props.form.trigger_type = enabled ? lastType.value : 'manual' }
watch(() => props.form.trigger_type, type => { if (type !== 'manual') lastType.value = type })
watch(() => [props.identity, props.form.saved_trigger_type], () => {
  lastType.value = scheduled.value ? props.form.trigger_type : props.form.saved_trigger_type || 'daily'
})
function invalidate() {
  sequence++; window.clearTimeout(timer); controller?.abort()
  times.value = []; error.value = ''; loading.value = false
}
watch(() => [props.identity, props.form.trigger_type, JSON.stringify(triggerConfig(props.form))], () => {
  invalidate()
  if (!['daily', 'weekly', 'monthly', 'cron'].includes(props.form.trigger_type)) return
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
    <legend>执行方式 <small v-if="scheduled && form.trigger_type !== 'interval'">北京时间</small></legend>
    <div class="plan-mode"><button type="button" :class="{ active: !scheduled }" :aria-pressed="!scheduled" @click="mode(false)">手动</button><button type="button" :class="{ active: scheduled }" :aria-pressed="scheduled" @click="mode(true)">定时</button></div>
    <template v-if="scheduled">
      <div class="plan-input-grid" :class="{ monthly: form.trigger_type === 'monthly' }">
      <label class="field plan-type-field"><span>定时方式</span><select v-model="form.trigger_type"><option v-if="form.trigger_type === 'cron'" value="cron" disabled hidden>保留原计划</option><option value="interval">间隔运行</option><option value="daily">每日</option><option value="weekly">每周</option><option value="monthly">每月</option></select></label>
      <label v-if="form.trigger_type === 'interval'" class="field"><span>间隔时长</span><input v-model.number="form.interval_value" type="number" min="1" max="100000" step="1" required /></label>
      <label v-if="form.trigger_type === 'interval'" class="field"><span>间隔单位</span><select v-model="form.interval_unit"><option value="seconds">秒</option><option value="minutes">分钟</option><option value="hours">小时</option><option value="days">天</option></select></label>
      <label v-if="form.trigger_type === 'monthly'" class="field"><span>执行日期</span><select v-model.number="form.monthly_day" required><option v-if="form.monthly_day !== 'last' && Number(form.monthly_day) > 1" :value="form.monthly_day" disabled hidden>原计划：每月 {{ form.monthly_day }} 号</option><option :value="1">每月一号</option><option value="last">每月最后一天</option></select></label>
      <label v-if="form.trigger_type === 'daily'" class="field"><span>执行时间</span><input v-model="form.daily_time" type="time" required /></label>
      <label v-else-if="form.trigger_type === 'weekly'" class="field"><span>执行时间</span><input v-model="form.weekly_time" type="time" required /></label>
      <label v-else-if="form.trigger_type === 'monthly'" class="field"><span>执行时间</span><input v-model="form.monthly_time" type="time" required /></label>
      <fieldset v-if="form.trigger_type === 'weekly'" class="plan-weekdays"><legend>执行星期</legend><label v-for="day in weekdayOptions" :key="day.value"><input v-model="form.weekly_days" type="checkbox" :value="day.value" />周{{ day.label }}</label></fieldset>
      </div>
      <p v-if="form.trigger_type === 'interval'" class="plan-help">首次启用后等待所设时长。本次运行结束后重新计时，手动运行也会重新计时；失败或取消后仍按间隔继续。排队和运行期间不会重复提交。</p>
      <p v-if="form.trigger_type === 'monthly' && Number(form.monthly_day) >= 29" class="plan-help">当月没有该日期时跳过。</p>
      <p v-if="form.trigger_type === 'cron'" class="plan-help">原计划保持不变，也可选择新的定时方式。</p>
      <p v-if="error" class="danger-text plan-help" role="alert">{{ error }}</p>
      <details v-if="form.trigger_type !== 'interval'" class="plan-times"><summary>未来 5 次 · 北京时间</summary><p v-if="loading" role="status">正在计算…</p><ol v-else><li v-for="point in times" :key="point">{{ formatTime(point) }}</li></ol></details>
    </template>
  </fieldset>
</template>

<style scoped>
.plan-fields { display: grid; gap: 12px; min-width: 0; margin: 0; padding: 0; border: 0; }
.plan-fields legend { width: 100%; padding: 0 0 6px; font-size: 13px; font-weight: 600; }
.plan-fields legend small { float: right; font-weight: 400; color: var(--muted); font-size: 11px; padding-top: 2px; }
.plan-mode { display: inline-grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px; padding: 4px; border: 1px solid var(--border); border-radius: 10px; background: #eef2ef; }
.plan-mode button { min-height: 32px; padding: 6px 14px; border: 0; border-radius: 7px; color: var(--muted); background: transparent; font: inherit; font-size: 13px; cursor: pointer; }
.plan-mode button.active { color: var(--brand-strong); background: var(--surface); box-shadow: 0 1px 4px rgba(30,60,38,.1); font-weight: 600; }
.plan-mode button:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.plan-input-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-items: start; }
.plan-input-grid.monthly .plan-type-field { grid-column: 1 / -1; }
.plan-weekdays { display: flex; grid-column: 1 / -1; flex-wrap: wrap; gap: 8px 12px; border: 0; padding: 0; margin: 0; min-width: 0; }
.plan-weekdays label { display: flex; gap: 4px; align-items: center; font-size: 12px; cursor: pointer; }
.plan-weekdays input { accent-color: var(--brand); margin: 0; width: 15px; height: 15px; }
.plan-fields select, .plan-fields input { min-width: 0; max-width: 100%; }
.plan-help, .plan-times { margin: 0; font-size: 12px; overflow-wrap: anywhere; }
.plan-help:not(.danger-text), .plan-times { color: var(--muted); }
.plan-times summary { cursor: pointer; }
.plan-times ol { margin: 8px 0 0; padding-left: 20px; line-height: 1.8; }
</style>
