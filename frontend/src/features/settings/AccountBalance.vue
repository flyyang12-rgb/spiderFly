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
    <header><h3 id="account-balance-title">DeepSeek 账户余额</h3><button class="button secondary" type="button" :disabled="!configured || disabled || pending" @click="refresh">{{ pending ? '正在查询…' : '刷新余额' }}</button></header>
    <template v-if="balance">
      <p v-if="error || !balance.is_available" class="balance-status unavailable">{{ error ? '结果已过期' : '余额不足' }}</p>
      <dl v-for="item in balance.balance_infos" :key="item.currency" class="balance-values">
        <div class="balance-total"><dt>可用余额 · {{ item.currency }}</dt><dd>{{ item.total_balance }}</dd></div>
        <div><dt>充值余额</dt><dd>{{ item.topped_up_balance }} {{ item.currency }}</dd></div>
        <div><dt>赠送余额</dt><dd>{{ item.granted_balance }} {{ item.currency }}</dd></div>
      </dl>
      <p class="balance-note">查询时间：{{ checkedTime(balance.checked_at) }}</p>
    </template>

    <p v-if="error" class="balance-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.account-balance{margin:0 24px 24px;padding:20px 0 0;border-top:1px solid #ECEFEC;display:grid;gap:12px;min-width:0}.account-balance header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.account-balance h3{font-size:14px;line-height:20px;margin:0;font-weight:600}.account-balance .button{min-width:104px}.account-balance .button:focus-visible{outline:2px solid #009139;outline-offset:2px}.balance-note,.balance-status,.balance-error{margin:0;font-size:12px;line-height:1.8;overflow-wrap:anywhere}.balance-note{color:#697b70}.balance-status{color:#276b43}.balance-status.unavailable{color:#8b4e25}.balance-error{color:#a13a2d}.balance-values{display:grid;grid-template-columns:2fr 1fr 1fr;gap:16px;margin:0}.balance-values>div{display:grid;align-content:start;gap:4px;min-width:0}.balance-values dt{font-size:12px;color:#697b70}.balance-values dd{font-size:13px;margin:0;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}.balance-total dd{font-size:24px;font-weight:600;line-height:32px}
@media(max-width:720px){.balance-values{grid-template-columns:1fr 1fr}.balance-total{grid-column:1/-1}}@media(max-width:480px){.account-balance{margin:0 16px 16px;padding:16px}}
</style>
