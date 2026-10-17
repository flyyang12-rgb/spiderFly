<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import AccountBalance from './features/settings/AccountBalance.vue'

const props = defineProps({ canEdit: Boolean })
const emit = defineEmits(['session-expired'])
const form = reactive({ model: '', api_key: '' })
const configured = ref(false)
const keySource = ref('none')
const savedModel = ref('')
const models = ref([])
const catalogVerified = ref(false)
const message = ref('')
const failed = ref(false)
const loaded = ref(false)
const operation = ref('')
const balanceRevision = ref(0)
let disposed = false
let generation = 0
let controller
let applying = false

const pending = computed(() => Boolean(operation.value))
const environmentKey = computed(() => keySource.value === 'environment')
const keySourceLabel = computed(() => ({ environment: '环境配置', saved: '已保存密钥', none: '未配置' })[keySource.value])
const canFetch = computed(() => props.canEdit && loaded.value && !pending.value
  && (environmentKey.value || Boolean(form.api_key.trim()) || configured.value))
const canSave = computed(() => props.canEdit && loaded.value && !pending.value
  && catalogVerified.value && models.value.includes(form.model))

function showMessage(text, error = false) {
  message.value = text
  failed.value = error
}

function cancelRequest() {
  generation += 1
  controller?.abort()
  controller = undefined
  operation.value = ''
}

function invalidateCatalog() {
  cancelRequest()
  catalogVerified.value = false
  models.value = []
  showMessage('密钥已修改，请重新获取模型列表。')
}

watch(() => form.api_key, () => {
  if (!applying) invalidateCatalog()
}, { flush: 'sync' })
watch(() => props.canEdit, () => {
  cancelRequest()
  applying = true
  form.api_key = ''
  applying = false
  catalogVerified.value = false
  showMessage('')
}, { flush: 'sync' })

function beginRequest(kind) {
  cancelRequest()
  controller = new AbortController()
  operation.value = kind
  return { generation, signal: controller.signal }
}

function isCurrent(requestState) {
  return !disposed && requestState.generation === generation && !requestState.signal.aborted
}

async function request(path, method, body, requestState) {
  const response = await fetch('/api/ai/settings' + path, {
    credentials: 'include', method, signal: requestState.signal,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  let data
  try { data = await response.json() } catch { data = {} }
  if (!isCurrent(requestState)) throw new DOMException('请求已失效', 'AbortError')
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : '模型配置请求失败，请重试。')
    error.status = response.status
    throw error
  }
  return data
}

function modelList(value) {
  return Array.isArray(value) ? [...new Set(value.filter(model => typeof model === 'string' && model.trim()))] : []
}

function applySaved(data, key = '') {
  applying = true
  form.model = typeof data.model === 'string' ? data.model : ''
  form.api_key = key
  applying = false
  savedModel.value = form.model
  configured.value = data.key_configured === true
  keySource.value = ['environment', 'saved'].includes(data.key_source) ? data.key_source : 'none'
  models.value = modelList(data.models)
  catalogVerified.value = false
}

async function revealKey(requestState) {
  if (!props.canEdit || !configured.value) return
  const data = await request('/key', 'GET', undefined, requestState)
  if (!isCurrent(requestState) || !props.canEdit) return
  applying = true
  form.api_key = typeof data.api_key === 'string' ? data.api_key : ''
  applying = false
  keySource.value = ['environment', 'saved'].includes(data.key_source) ? data.key_source : 'none'
}

function handleFailure(error, requestState, prefix, submittedKey = '') {
  if (!isCurrent(requestState) || error.name === 'AbortError') return
  if (error.status === 401) {
    applying = true
    form.api_key = ''
    applying = false
    catalogVerified.value = false
    loaded.value = false
    emit('session-expired')
    return
  }
  let detail = error.message || '请求失败，请重试。'
  for (const secret of [submittedKey, form.api_key].filter(Boolean)) detail = detail.replaceAll(secret, '[已隐藏]')
  showMessage(prefix + detail, true)
}

function finishRequest(requestState) {
  if (isCurrent(requestState)) {
    operation.value = ''
    controller = undefined
  }
}

async function fetchModels() {
  if (!canFetch.value) return
  const submittedKey = environmentKey.value ? '' : form.api_key
  const requestState = beginRequest('models')
  catalogVerified.value = false
  showMessage('')
  try {
    const data = await request('/models', 'POST', { api_key: submittedKey }, requestState)
    if (!isCurrent(requestState)) return
    const available = modelList(data.models)
    if (data.connected !== true || !available.length) throw new Error('未获取到可选模型，请检查密钥后重试。')
    models.value = available
    catalogVerified.value = true
    if (!available.includes(form.model)) {
      form.model = ''
    }
  } catch (error) { handleFailure(error, requestState, '获取模型列表失败：', submittedKey) }
  finally { finishRequest(requestState) }
}

async function save() {
  if (!canSave.value) return
  const displayedKey = form.api_key
  const submittedKey = environmentKey.value ? '' : displayedKey
  const requestState = beginRequest('save')
  showMessage('')
  try {
    const data = await request('', 'PUT', { model: form.model, api_key: submittedKey }, requestState)
    if (!isCurrent(requestState)) return
    applySaved(data, displayedKey)
    showMessage('配置已保存。')
    if (!displayedKey) {
      try { await revealKey(requestState) }
      catch (error) { handleFailure(error, requestState, '配置已保存，密钥读取失败：') }
    }
    if (isCurrent(requestState) && loaded.value) balanceRevision.value += 1
  } catch (error) { handleFailure(error, requestState, '保存失败：', submittedKey) }
  finally { finishRequest(requestState) }
}

onMounted(async () => {
  const requestState = beginRequest('load')
  try {
    const data = await request('', 'GET', undefined, requestState)
    if (!isCurrent(requestState)) return
    applySaved(data)
    loaded.value = true
    await revealKey(requestState)
  } catch (error) { handleFailure(error, requestState, '加载配置失败：') }
  finally { finishRequest(requestState) }
})
onUnmounted(() => {
  disposed = true
  cancelRequest()
  applying = true
  form.api_key = ''
})
</script>

<template>
  <section class="panel ai-settings" :aria-busy="pending">
    <header class="panel-heading"><h2>API 密钥配置</h2><span class="mini-badge" :class="configured ? 'success-badge' : 'neutral-badge'">{{ configured ? '已配置' : '未配置' }}</span></header>
    <form @submit.prevent="save">
      <template v-if="canEdit">
        <label class="field"><span>DeepSeek API 密钥{{ environmentKey ? '（环境配置）' : '' }}</span><input v-model="form.api_key" :disabled="!loaded || operation === 'save' || environmentKey" type="text" autocomplete="off" spellcheck="false" maxlength="500" :placeholder="configured ? '留空保留当前密钥' : '输入 API 密钥'" /></label>
        <div class="ai-fetch-row"><button class="button secondary" type="button" :disabled="!canFetch" @click="fetchModels">{{ operation === 'models' ? '正在获取…' : '获取模型列表' }}</button></div>
        <label class="field"><span>默认模型</span><select v-model="form.model" :disabled="!catalogVerified || pending || !loaded"><option value="" disabled>请先获取模型列表</option><option v-if="form.model && !models.includes(form.model)" :value="form.model" disabled>{{ form.model }}（需刷新列表）</option><option v-for="model in models" :key="model" :value="model">{{ model }}</option></select></label>
      </template>
      <div v-else class="ai-current-config"><div><span>默认模型</span><strong>{{ loaded ? savedModel || '尚未设置' : '正在加载…' }}</strong></div><div><span>密钥来源</span><strong>{{ keySourceLabel }}</strong></div></div>
      <p v-if="message" class="ai-setting-message" :class="{ failed }" :role="failed ? 'alert' : 'status'">{{ message }}</p>
      <div v-if="canEdit" class="ai-setting-actions"><button class="button primary" :disabled="!canSave">{{ operation === 'save' ? '正在保存…' : '保存配置' }}</button></div>
    </form>
    <AccountBalance v-if="loaded" :configured="configured" :revision="balanceRevision" :disabled="operation === 'save'" @session-expired="emit('session-expired')" />
  </section>
</template>

<style scoped>
.ai-settings .panel-heading h2{margin:0}
.ai-settings form{padding:24px;display:grid;gap:16px;min-width:0}.ai-settings .field{min-width:0}.ai-settings input,.ai-settings select{width:100%;min-width:0;max-width:100%}.ai-setting-note{color:#697b70;font-size:13px;line-height:1.8;margin:0;overflow-wrap:anywhere}.ai-fetch-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.ai-fetch-row .button{min-width:128px}.ai-setting-actions{display:flex;justify-content:flex-end;gap:12px}.ai-setting-actions .button{min-width:112px}.ai-setting-message{margin:0;color:#276b43;font-size:13px;line-height:1.6;overflow-wrap:anywhere}.ai-setting-message.failed{color:#a13a2d}.ai-current-config{display:grid;gap:16px}.ai-current-config>div{display:grid;gap:8px;font-size:13px;overflow-wrap:anywhere}.ai-current-config>div>span{color:#697b70}.ai-current-config strong{font-weight:500}.ai-settings :is(button,input,select):focus-visible{outline:2px solid #009139;outline-offset:2px}
@media(max-width:480px){.ai-settings form{padding:16px}.ai-setting-actions .button{width:100%}}
</style>
