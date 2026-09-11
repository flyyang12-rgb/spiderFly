<script setup>
import { onMounted, reactive, ref } from 'vue'
const props = defineProps({ canEdit: Boolean })
const form = reactive({ model: 'deepseek-v4-pro', max_seconds: 900, max_calls: 16, max_tokens: 100000, api_key: '' })
const configured = ref(false)
const models = ref([])
const message = ref('')
const busy = ref(false)
const failed = ref(false)
const maintenance = reactive({ unlimited: false, daily_seconds: 7200, daily_tokens: 200000 })
async function maintenanceRequest(method = 'GET') {
  const response = await fetch('/api/maintenance/settings', { method, credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: method === 'PUT' ? JSON.stringify(maintenance) : undefined })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '维护预算请求失败')
  Object.assign(maintenance, data)
}
async function saveMaintenance() {
  busy.value = true; failed.value = false
  try { await maintenanceRequest('PUT'); message.value = '维护预算已保存' }
  catch (error) { message.value = error.message; failed.value = true }
  finally { busy.value = false }
}
async function request(path, method = 'GET', body) {
  const response = await fetch('/api/ai/settings' + path, { credentials: 'include', method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '模型配置请求失败')
  return data
}
function apply(data) {
  for (const key of ['model', 'max_seconds', 'max_calls', 'max_tokens']) form[key] = data[key]
  configured.value = data.key_configured
  models.value = data.models
  form.api_key = ''
}
async function save() {
  busy.value = true; failed.value = false
  try { apply(await request('', 'PUT', { ...form })); message.value = '模型配置已保存' }
  catch (error) { message.value = error.message; failed.value = true }
  finally { form.api_key = ''; busy.value = false }
}
async function test() {
  busy.value = true; failed.value = false
  try { const data = await request('/test', 'POST'); message.value = 'DeepSeek 连接正常，可用模型：' + data.models.join('、') }
  catch (error) { message.value = error.message; failed.value = true }
  finally { busy.value = false }
}
onMounted(async () => { try { apply(await request('')); await maintenanceRequest() } catch (error) { message.value = error.message; failed.value = true } })
</script>
<template>
  <section class="panel ai-settings">
    <header class="panel-heading"><h2>AI 模型</h2><span class="mini-badge" :class="configured ? 'success-badge' : 'neutral-badge'">{{ configured ? '已配置' : '未配置' }}</span></header>
    <form @submit.prevent="save">
      <label class="field"><span>默认模型</span><select v-model="form.model" :disabled="!canEdit"><option v-for="model in models" :key="model" :value="model">{{ model }}</option></select></label>
      <label v-if="canEdit" class="field"><span>API 密钥</span><input v-model="form.api_key" type="password" autocomplete="new-password" maxlength="500" placeholder="留空保留原密钥" /></label>
      <details class="ai-budget-details"><summary>调用与维护预算</summary>
      <h3>每轮对话</h3>
      <div class="ai-budget-fields">
        <label class="field"><span>时长上限（秒）</span><input v-model.number="form.max_seconds" type="number" min="30" max="7200" :disabled="!canEdit" /></label>
        <label class="field"><span>调用次数</span><input v-model.number="form.max_calls" type="number" min="1" max="128" :disabled="!canEdit" /></label>
        <label class="field"><span>Token 上限</span><input v-model.number="form.max_tokens" type="number" min="1000" max="2000000" step="1000" :disabled="!canEdit" /></label>
      </div>
      <h3>自动维护 · 滚动 24 小时</h3>
      <label><input v-model="maintenance.unlimited" type="checkbox" :disabled="!canEdit" /> 不限制累计维护预算</label>
      <div class="ai-budget-fields">
        <label class="field"><span>全局时长上限（秒）</span><input v-model.number="maintenance.daily_seconds" type="number" min="60" max="86400" :disabled="!canEdit || maintenance.unlimited" /></label>
        <label class="field"><span>全局 Token 上限</span><input v-model.number="maintenance.daily_tokens" type="number" min="1000" max="2000000" :disabled="!canEdit || maintenance.unlimited" /></label>
      </div>
      <p class="ai-setting-note">{{ maintenance.unlimited ? '不按累计时长或 Token 额度拦截维护，单次运行超时仍生效。' : '单任务最多 45 分钟，额度不足时停止维护并通知。' }}</p>
      <div v-if="canEdit"><button class="button secondary" type="button" :disabled="busy" @click="saveMaintenance">保存维护预算</button></div>
      </details>
      <p v-if="message" class="ai-setting-message" :class="{ failed }" role="status">{{ message }}</p>
      <div v-if="canEdit" class="ai-setting-actions"><button class="button primary" :disabled="busy">保存配置</button><button class="button ghost" type="button" :disabled="busy || !configured" @click="test">测试连接</button></div>
      <p v-else class="ai-setting-note">模型配置由超级管理员维护。</p>
    </form>
  </section>
</template>
<style scoped>
.ai-budget-details{border-top:1px solid #e1e8e3;padding-top:14px}.ai-budget-details>summary{cursor:pointer;color:#4c6958;font-size:14px}.ai-budget-details h3{font-size:13px;margin:22px 0 12px;font-weight:500}.ai-budget-details .ai-setting-note{margin:14px 0}.ai-settings .panel-heading h2{margin:0}
.ai-settings form{padding:24px;display:grid;gap:20px}.ai-budget-fields{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.ai-setting-note{color:#697b70;font-size:13px;line-height:1.8;margin:0}.ai-setting-actions{display:flex;gap:12px}.ai-setting-message{padding:12px;border-radius:8px;background:#edf7ef;color:#276b43;overflow-wrap:anywhere}.ai-setting-message.failed{background:#fff0ec;color:#a13a2d}@media(max-width:700px){.ai-budget-fields{grid-template-columns:1fr}}
</style>
