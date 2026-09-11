<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
const props = defineProps({ canEdit: Boolean })
const form = reactive({ model: 'deepseek-v4-pro', api_key: '' })
const configured = ref(false)
const models = ref([])
const message = ref('')
const busy = ref(false)
const loaded = ref(false)
const failed = ref(false)
let messageTimer
function showMessage(text, error = false) {
  clearTimeout(messageTimer)
  message.value = text
  failed.value = error
  if (!error) messageTimer = setTimeout(() => { message.value = '' }, 3600)
}
onUnmounted(() => clearTimeout(messageTimer))
async function request(path, method = 'GET', body) {
  const response = await fetch('/api/ai/settings' + path, { credentials: 'include', method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '模型配置请求失败')
  return data
}
function apply(data) {
  form.model = data.model
  configured.value = data.key_configured
  models.value = data.models
  form.api_key = ''
}
async function save() {
  if (!props.canEdit || busy.value || !loaded.value) return
  busy.value = true
  try {
    apply(await request('', 'PUT', { ...form }))
    showMessage('配置已保存')
  }
  catch (error) { showMessage('保存失败：' + error.message, true) }
  finally { form.api_key = ''; busy.value = false }
}
async function test() {
  busy.value = true
  try { await request('/test', 'POST'); showMessage('连接正常') }
  catch (error) { showMessage(error.message, true) }
  finally { busy.value = false }
}
onMounted(async () => { try { apply(await request('')); loaded.value = true } catch (error) { showMessage(error.message, true) } })
</script>
<template>
  <section class="panel ai-settings">
    <header class="panel-heading"><h2>AI 模型</h2><span class="mini-badge" :class="configured ? 'success-badge' : 'neutral-badge'">{{ configured ? '已配置' : '未配置' }}</span></header>
    <form @submit.prevent="save">
      <label class="field"><span>默认模型</span><select v-model="form.model" :disabled="!canEdit || busy || !loaded"><option v-for="model in models" :key="model" :value="model">{{ model }}</option></select></label>
      <label v-if="canEdit" class="field"><span>API 密钥</span><input v-model="form.api_key" :disabled="busy || !loaded" type="password" autocomplete="new-password" maxlength="500" placeholder="留空保留原密钥" /></label>
      <p class="ai-setting-note">每轮对话调用 AI 最长 20 分钟，到时自动停止。</p>
      <p v-if="message" class="ai-setting-message" :class="{ failed }" role="status">{{ message }}</p>
      <div v-if="canEdit" class="ai-setting-actions"><button class="button primary" :disabled="busy || !loaded">{{ busy ? '处理中…' : '保存配置' }}</button><button class="button ghost" type="button" :disabled="busy || !configured" @click="test">测试连接</button></div>
      <p v-else class="ai-setting-note">模型配置由超级管理员维护。</p>
    </form>
  </section>
</template>
<style scoped>
.ai-settings .panel-heading h2{margin:0}
.ai-settings form{padding:24px;display:grid;gap:20px}.ai-setting-note{color:#697b70;font-size:13px;line-height:1.8;margin:0}.ai-setting-actions{display:flex;gap:12px}.ai-setting-message{margin:0;color:#276b43;font-size:13px;overflow-wrap:anywhere}.ai-setting-message.failed{color:#a13a2d}
</style>
