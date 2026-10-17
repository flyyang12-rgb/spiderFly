<script setup>
import { computed, onBeforeUnmount, ref, useId, watch } from 'vue'
import { request } from '../../lib/api'
import { cronPresets } from '../../lib/constants'
import { formatTime } from '../../lib/format'

const props = defineProps({ modelValue: { type: String, default: '' }, disabled: Boolean, compact: Boolean })
const emit = defineEmits(['update:modelValue'])
const fieldId = useId()
const times = ref([])
const error = ref('')
const loading = ref(false)
const preset = computed(() => cronPresets.some((item) => item.value === props.modelValue) ? props.modelValue : '')
let generation = 0
let timer = null
let controller = null

function invalidate() {
  generation += 1
  window.clearTimeout(timer)
  controller?.abort()
  times.value = []
  error.value = ''
  loading.value = false
}

function updateExpression(value) {
  if (value === props.modelValue) return
  invalidate()
  emit('update:modelValue', value)
}

watch(() => props.modelValue, (value) => {
  invalidate()
  if (!value.trim()) return
  const requestGeneration = generation
  loading.value = true
  timer = window.setTimeout(async () => {
    controller = new AbortController()
    try {
      const result = await request('/schedules/preview', {
        method: 'POST',
        body: JSON.stringify({ expression: value }),
        signal: controller.signal,
      })
      if (requestGeneration !== generation || value !== props.modelValue) return
      times.value = Array.isArray(result.times) ? result.times : []
    } catch (failure) {
      if (requestGeneration !== generation || failure.name === 'AbortError') return
      error.value = failure.message
    } finally {
      if (requestGeneration === generation) loading.value = false
    }
  }, 300)
}, { immediate: true })

onBeforeUnmount(invalidate)
</script>

<template>
  <div class="cron-editor">
    <label class="field">
      <span>常用计划</span>
      <select :value="preset" :disabled="disabled" @change="$event.target.value && updateExpression($event.target.value)">
        <option value="">自定义</option>
        <option v-for="item in cronPresets" :key="item.value" :value="item.value">{{ item.label }}</option>
      </select>
    </label>
    <label class="field" :for="fieldId">
      <span>Cron 表达式</span>
      <input :id="fieldId" :value="modelValue" :disabled="disabled" type="text" maxlength="1024"
        class="code-input" placeholder="0 9,14 * * *" autocomplete="off" spellcheck="false"
        :aria-invalid="Boolean(error)" :aria-describedby="fieldId + '-help'"
        @input="updateExpression($event.target.value)" />
      <small :id="fieldId + '-help'">分 · 时 · 日 · 月 · 星期。0/7 为周日；日与星期只限制一项。</small>
    </label>
    <p v-if="compact && error" class="danger-text" role="alert">{{ error }}</p>
    <component :is="compact ? 'details' : 'div'" class="cron-preview" aria-live="polite" :aria-busy="loading">
      <component :is="compact ? 'summary' : 'strong'">未来 5 次 · 北京时间</component>
      <p v-if="loading">正在计算…</p>
      <p v-else-if="error" class="danger-text" role="alert">{{ error }}</p>
      <ol v-else-if="times.length">
        <li v-for="point in times" :key="point">{{ formatTime(point) }}</li>
      </ol>
      <p v-else>请输入表达式</p>
    </component>
  </div>
</template>

<style scoped>
.cron-editor { display: grid; gap: 12px; }
.cron-preview { display: grid; gap: 6px; padding: 12px 0 0; border: 0; border-top: 1px solid var(--border-soft); border-radius: 0; background: transparent; }
.cron-preview strong { color: var(--ink); font-size: 12px; }
.cron-preview summary { cursor: pointer; color: var(--muted); font-size: 12px; }
.cron-preview p { margin: 0; color: var(--muted); font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }
.cron-preview p.danger-text { color: var(--coral); }
.cron-preview ol { display: grid; gap: 4px; margin: 0; padding-left: 22px; color: var(--muted); font-size: 11px; line-height: 1.5; }
</style>
