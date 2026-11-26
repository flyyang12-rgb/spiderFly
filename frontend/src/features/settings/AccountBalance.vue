<script setup>
import { onUnmounted, ref, watch } from 'vue'

const props = defineProps({ configured: Boolean, revision: Number, disabled: Boolean })
const emit = defineEmits(['session-expired'])
const balance = ref(null)
const pending = ref(false)
const error = ref('')
let generation = 0
let controller
let disposed = false

function cancel() {
  generation += 1
  controller?.abort()
  controller = undefined
  pending.value = false
}

function reset() {
  cancel()
  balance.value = null
  error.value = ''
}

async function refresh() {
  if (!props.configured || props.disabled || pending.value || disposed) return
  cancel()
  const current = generation
  controller = new AbortController()
  pending.value = true
  error.value = ''
  try {
    const response = await fetch('/api/ai/settings/balance', {
      credentials: 'include', signal: controller.signal,
    })
    let data
    try { data = await response.json() } catch { data = {} }
    if (disposed || current !== generation) return
    if (response.status === 401 || response.status === 403) {
      balance.value = null
      if (response.status === 401) emit('session-expired')
      throw new Error('登录或访问权限已变化，请重新登录后查询。')
    }
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '余额查询失败，请稍后重试。')
    if (!Array.isArray(data.balance_infos) || !data.balance_infos.length || typeof data.is_available !== 'boolean' || !data.checked_at) {
      throw new Error('余额数据无效，请稍后重试。')
    }
    balance.value = data
  } catch (failure) {
    if (!disposed && current === generation && failure.name !== 'AbortError') {
      error.value = failure.message || '余额查询失败，请稍后重试。'
    }
  } finally {
    if (!disposed && current === generation) {
      pending.value = false
      controller = undefined
    }
  }
}

watch(() => props.configured, reset, { flush: 'sync' })
watch(() => props.revision, () => { reset(); refresh() }, { flush: 'post' })
onUnmounted(() => { disposed = true; reset() })

function checkedTime(value) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <section class="account-balance" aria-labelledby="account-balance-title" :aria-busy="pending">
    <header>
      <h3 id="account-balance-title">DeepSeek 账户余额</h3>
      <div class="balance-summary">
        <span v-if="balance">查询时间 · {{ checkedTime(balance.checked_at) }}</span>
        <span v-else-if="!configured">请先配置密钥</span>
        <span v-else-if="!error && !pending">尚未查询</span>
        <button class="button secondary" type="button" :disabled="!configured || disabled || pending" @click="refresh">{{ pending ? '正在查询…' : '刷新余额' }}</button>
      </div>
    </header>
    <template v-if="balance">
      <p v-if="error || !balance.is_available" class="balance-status unavailable">{{ error ? '结果已过期' : '余额不足' }}</p>
      <dl v-for="item in balance.balance_infos" :key="item.currency" class="balance-values">
        <div class="balance-total"><dt>可用余额 · {{ item.currency }}</dt><dd>{{ item.total_balance }}</dd></div>
        <div><dt>充值余额</dt><dd>{{ item.topped_up_balance }} {{ item.currency }}</dd></div>
        <div><dt>赠送余额</dt><dd>{{ item.granted_balance }} {{ item.currency }}</dd></div>
      </dl>
    </template>

    <p v-if="error" class="balance-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.account-balance { display: grid; min-width: 0; gap: 12px; }
.account-balance header { display: grid; gap: 7px; }
.account-balance h3 { margin: 0; color: var(--text); font-size: var(--workspace-body); font-weight: 500; }
.balance-summary { display: flex; align-items: center; gap: 10px; }
.balance-summary span { color: var(--muted); font-size: var(--workspace-caption); overflow-wrap: anywhere; }
.balance-summary .button { min-width: 104px; min-height: var(--field-height, 38px); margin-left: auto; }
.balance-status, .balance-error { margin: 0; font-size: var(--workspace-caption); overflow-wrap: anywhere; }
.balance-status.unavailable { color: #8b4e25; }
.balance-error { color: #a13a2d; }
.balance-values { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 16px; margin: 0; }
.balance-values > div { display: grid; min-width: 0; align-content: start; gap: 4px; }
.balance-values dt { font-size: var(--workspace-caption); color: var(--muted); }
.balance-values dd { margin: 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.balance-total dd { font-size: 24px; font-weight: 600; line-height: 32px; }
@media (max-width: 720px) {
  .balance-values { grid-template-columns: 1fr 1fr; }
  .balance-total { grid-column: 1 / -1; }
}
</style>
