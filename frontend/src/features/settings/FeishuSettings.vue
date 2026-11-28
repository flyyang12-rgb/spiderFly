<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
const props = defineProps({ canEdit: Boolean })
const emit = defineEmits(['session-expired', 'saved'])
const form = reactive({ mode: 'webhook', webhook_url: '', webhook_secret: '', clear_webhook_secret: false, app_id: '', app_secret: '', receiver_id: '', receiver_id_type: 'mobile' })
const saved = ref(null)
const loaded = ref(false)
const operation = ref('')
const message = ref('')
const failed = ref(false)
const confirmClear = ref(false)
let disposed = false
let generation = 0
let controller
const pending = computed(() => Boolean(operation.value))
const readOnly = computed(() => !props.canEdit || saved.value?.read_only === true)
const canSave = computed(() => loaded.value && !readOnly.value && !pending.value && !saved.value?.configuration_error && (form.mode === 'webhook' ? Boolean(form.webhook_url.trim() || saved.value?.webhook_url_configured) : Boolean(form.app_id.trim() && form.receiver_id.trim() && (form.app_secret.trim() || (saved.value?.app_secret_configured && form.app_id === saved.value?.app_id)))))
const sourceLabel = computed(() => ({ environment: '部署环境', saved: '本机配置', none: '未配置' })[saved.value?.source] || '未配置')
function wipeSecrets() { form.webhook_url = ''; form.webhook_secret = ''; form.app_secret = ''; form.clear_webhook_secret = false }
watch(form, () => { if (!failed.value) message.value = ''; confirmClear.value = false }, { flush: 'sync' })
function cancel() { generation += 1; controller?.abort(); controller = undefined; operation.value = '' }
function begin(kind) { cancel(); controller = new AbortController(); operation.value = kind; return { generation, signal: controller.signal } }
function current(state) { return !disposed && generation === state.generation && !state.signal.aborted }
function apply(data) {
  saved.value = data; form.mode = data.mode === 'application' ? 'application' : 'webhook'
  form.app_id = props.canEdit && typeof data.app_id === 'string' ? data.app_id : ''
  form.receiver_id = props.canEdit && typeof data.receiver_id === 'string' ? data.receiver_id : ''
  form.receiver_id_type = data.receiver_id_type === 'open_id' ? 'open_id' : 'mobile'
  wipeSecrets()
  if (props.canEdit) {
    for (const name of ['webhook_url', 'webhook_secret', 'app_secret']) form[name] = typeof data[name] === 'string' ? data[name] : ''
  }
  confirmClear.value = false; loaded.value = true
}
async function request(method, body, state, suffix = '') {
  const response = await fetch('/api/settings/feishu' + suffix, { credentials: 'include', method, signal: state.signal, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined })
  let data
  try { data = await response.json() } catch { data = {} }
  if (!current(state)) throw new DOMException('请求已失效', 'AbortError')
  if (!response.ok) { const error = new Error(typeof data.detail === 'string' ? data.detail : '飞书配置请求失败'); error.status = response.status; throw error }
  if (typeof data.configured !== 'boolean' || typeof data.revision !== 'string') throw new Error('飞书配置返回格式无效')
  return data
}
function failure(error, state, secrets = []) {
  if (!current(state) || error.name === 'AbortError') return
  if (error.status === 401 || error.status === 403) { wipeSecrets(); form.app_id = ''; form.receiver_id = ''; saved.value = null; loaded.value = false; if (error.status === 401) emit('session-expired') }
  let detail = error.status === 403 ? '访问权限已变化，请重新登录' : error.message || '请求失败，请重试'
  for (const secret of [...secrets, form.webhook_url, form.webhook_secret, form.app_secret].filter(Boolean)) detail = detail.replaceAll(secret, '[已隐藏]')
  message.value = detail; failed.value = true
}
function finish(state) { if (current(state)) { operation.value = ''; controller = undefined } }
async function load() {
  const state = begin('load'); message.value = ''; failed.value = false
  try { const data = await request('GET', undefined, state, props.canEdit ? '/credentials' : ''); if (current(state)) apply(data) }
  catch (error) { failure(error, state) }
  finally { finish(state) }
}
async function save() {
  if (!canSave.value) return
  const body = { revision: saved.value.revision, mode: form.mode }
  if (form.mode === 'webhook') Object.assign(body, { webhook_url: form.webhook_url, webhook_secret: form.clear_webhook_secret ? '' : form.webhook_secret, clear_webhook_secret: form.clear_webhook_secret })
  else Object.assign(body, { app_id: form.app_id.trim(), app_secret: form.app_secret, receiver_id: form.receiver_id.trim(), receiver_id_type: form.receiver_id_type })
  const secrets = [body.webhook_url, body.webhook_secret, body.app_secret].filter(Boolean)
  const state = begin('save'); message.value = ''; failed.value = false
  try {
    const data = await request('PUT', body, state)
    const details = await request('GET', undefined, state, '/credentials')
    if (details.revision !== data.revision) throw new Error('配置已被其他操作更新，请重新加载')
    if (current(state)) { apply(details); message.value = '配置已保存'; emit('saved', data) }
  }
  catch (error) { failure(error, state, secrets) }
  finally { finish(state) }
}
async function clear() {
  if (!loaded.value || readOnly.value || pending.value || !confirmClear.value || saved.value?.source !== 'saved') return
  const state = begin('clear'); message.value = ''; failed.value = false
  try { const data = await request('DELETE', { revision: saved.value.revision }, state); if (current(state)) { apply(data); message.value = '配置已清除'; emit('saved', data) } }
  catch (error) { failure(error, state) }
  finally { finish(state) }
}
watch(() => props.canEdit, () => { cancel(); wipeSecrets(); form.app_id = ''; form.receiver_id = ''; saved.value = null; loaded.value = false; confirmClear.value = false; load() }, { flush: 'sync' })
onMounted(load)
onUnmounted(() => { disposed = true; cancel(); wipeSecrets(); form.app_id = ''; form.receiver_id = '' })
</script>

<template>
  <section class="panel feishu-settings" :aria-busy="pending" aria-labelledby="feishu-settings-title">
    <header class="panel-heading service-config-heading">
      <h2 id="feishu-settings-title">飞书通知</h2>
      <span class="mini-badge" :class="saved?.configured ? 'success-badge' : 'neutral-badge'">{{ saved?.configuration_error ? '配置异常' : loaded ? saved?.configured ? '已配置' : '未配置' : '加载中' }}</span>
      <fieldset v-if="canEdit" class="notification-choice" :disabled="!loaded || pending || readOnly">
        <legend class="sr-only">通知方式</legend>
        <label><input v-model="form.mode" class="sr-only" type="radio" value="webhook" name="feishu-mode" /><span>群通知</span></label>
        <label><input v-model="form.mode" class="sr-only" type="radio" value="application" name="feishu-mode" /><span>个人通知</span></label>
      </fieldset>
    </header>
    <form class="service-config-form" @submit.prevent="save">
      <template v-if="canEdit">
        <template v-if="form.mode === 'webhook'">
          <label class="field credential-field"><span>Webhook 地址</span><input v-model="form.webhook_url" type="text" autocomplete="off" spellcheck="false" maxlength="2048" :readonly="readOnly" :disabled="!loaded || pending" placeholder="粘贴群机器人的 Webhook 地址" /></label>
          <label class="field credential-field"><span>签名密钥 <small>可选</small></span><input v-model="form.webhook_secret" type="text" autocomplete="off" spellcheck="false" maxlength="500" :readonly="readOnly" :disabled="!loaded || pending || form.clear_webhook_secret" placeholder="未开启签名校验可留空" /></label>
          <label v-if="saved?.webhook_secret_configured && !readOnly" class="clear-signature"><input v-model="form.clear_webhook_secret" type="checkbox" :disabled="pending" />清除已保存的签名密钥</label>
        </template>
        <template v-else>
          <label class="field credential-field"><span>App ID</span><input v-model="form.app_id" autocomplete="off" spellcheck="false" maxlength="200" :readonly="readOnly" :disabled="!loaded || pending" /></label>
          <label class="field credential-field"><span>App Secret</span><input v-model="form.app_secret" type="text" autocomplete="off" spellcheck="false" maxlength="500" :readonly="readOnly" :disabled="!loaded || pending" placeholder="输入应用密钥" /></label>
          <div class="recipient-row">
            <label class="field"><span>收件人类型</span><select v-model="form.receiver_id_type" :disabled="!loaded || pending || readOnly"><option value="mobile">手机号</option><option value="open_id">Open ID</option></select></label>
            <label class="field"><span>{{ form.receiver_id_type === 'open_id' ? '收件人 Open ID' : '收件人手机号' }}</span><input v-model="form.receiver_id" :inputmode="form.receiver_id_type === 'mobile' ? 'tel' : 'text'" autocomplete="off" spellcheck="false" maxlength="200" :readonly="readOnly" :disabled="!loaded || pending" /></label>
          </div>
        </template>
      </template>
      <dl v-else-if="loaded" class="service-status"><div><dt>通知方式</dt><dd>{{ saved?.mode === 'application' ? '个人通知' : '群通知' }}</dd></div><div><dt>配置来源</dt><dd>{{ sourceLabel }}</dd></div></dl>
      <p v-if="saved?.configuration_error" class="service-message failed" role="alert">{{ saved.configuration_error }}</p>
      <p v-if="message" class="service-message" :class="{ failed }" :role="failed ? 'alert' : 'status'">{{ message }}</p>
      <div v-if="failed && !pending" class="reload-config"><button class="button ghost" type="button" @click="load">重新加载</button></div>
      <template v-if="canEdit && !saved?.read_only">
        <div v-if="confirmClear" class="service-config-actions clear-confirm"><p>清除群通知和个人通知配置？</p><button class="button danger" type="button" :disabled="pending" @click="clear">{{ operation === 'clear' ? '正在清除…' : '确认清除' }}</button><button class="button ghost" type="button" :disabled="pending" @click="confirmClear = false">取消</button></div>
        <div v-else class="service-config-actions"><button v-if="saved?.source === 'saved'" class="button danger clear-config" type="button" :disabled="pending" @click="confirmClear = true">清除配置</button><button class="button primary" :disabled="!canSave">{{ operation === 'save' ? '正在保存…' : '保存配置' }}</button></div>
      </template>
    </form>
  </section>
</template>

<style scoped>
.notification-choice {
  display: inline-flex;
  min-width: 0;
  margin: 0 0 0 auto;
  padding: 2px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-subtle);
}
.notification-choice label { position: relative; }
.notification-choice label span {
  display: block;
  padding: 3px 9px;
  border-radius: 5px;
  color: var(--muted);
  font-size: var(--workspace-caption);
  white-space: nowrap;
  cursor: pointer;
}
.notification-choice input:checked + span { background: var(--surface); color: var(--brand-strong); font-weight: 600; box-shadow: 0 1px 3px #2038282b; }
.notification-choice input:focus-visible + span { outline: 2px solid var(--brand); }
.notification-choice:disabled span { cursor: default; opacity: .65; }
.recipient-row { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 10px; }
.recipient-row .field select { min-width: 92px; }
.field small, .clear-signature { color: var(--muted); font-size: var(--workspace-caption); }
.field small { margin-left: 4px; font-weight: 400; }
.clear-signature { display: flex; align-items: center; gap: 8px; }
.clear-signature input { width: 16px; height: 16px; accent-color: var(--brand); }
.clear-config { margin-right: auto; }
.service-message { margin: 0; font-size: var(--workspace-caption); color: var(--brand-strong); overflow-wrap: anywhere; }
.service-message.failed { color: #a13a2d; }
.reload-config .button { padding-left: 0; }
.service-status { display: grid; gap: 18px; margin: 0; }
.service-status > div { display: flex; justify-content: space-between; gap: 16px; }
.service-status dt { color: var(--muted); }
.service-status dd { margin: 0; }
.clear-confirm { flex-wrap: wrap; }
.clear-confirm p { margin: 0 auto 0 0; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
.feishu-settings :is(button, input, select):focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
</style>
