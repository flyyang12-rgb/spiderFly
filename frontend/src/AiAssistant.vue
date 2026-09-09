<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AiMaintenance from './AiMaintenance.vue'
const props = defineProps({ taskId: { type: Number, default: null } })
const emit = defineEmits(['close', 'use-draft', 'open-execution'])
const thread = ref(null)
const threads = ref([])
const message = ref('')
const error = ref('')
const busy = ref(false)
const selectedDraft = ref(null)
const setting = ref(null)
const showHistory = ref(false)
const visibleDrafts = computed(() => showHistory.value ? thread.value?.drafts || [] : (thread.value?.drafts || []).slice(0, 1))
let timer
let alive = true
let generation = 0
const active = computed(() => thread.value?.turns?.find(item => ['pending', 'running'].includes(item.status)))
const latest = computed(() => thread.value?.turns?.[0])
const tokens = computed(() => (thread.value?.turns || []).reduce((sum, item) => sum + item.input_tokens + item.output_tokens, 0))
const labels = { pending: '等待 AI 处理', running: 'AI 正在处理', completed: '本轮完成', failed: '本轮未完成', cancelled: '已停止', interrupted: '已中断' }

async function api(path, body, method) {
  const response = await fetch('/api/ai' + path, { credentials: 'include', method: method || (body ? 'POST' : 'GET'),
    headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined })
  const value = await response.json()
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'AI 请求失败，请重试')
  return value
}

async function load(id = thread.value?.id) {
  if (!id) return
  const token = ++generation
  const value = await api('/threads/' + id)
  if (alive && token === generation) thread.value = value
}

async function newThread() {
  busy.value = true
  error.value = ''
  try {
    const created = await api('/threads', props.taskId ? { task_id: props.taskId } : {})
    selectedDraft.value = null
    showHistory.value = false
    await load(created.id)
    threads.value = await api('/threads')
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}

async function selectThread(id) {
  busy.value = true
  try { selectedDraft.value = null; showHistory.value = false; await load(id) }
  catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}

async function send() {
  if (!message.value.trim() || active.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await api('/threads/' + thread.value.id + '/messages', { content: message.value })
    message.value = ''
    await load()
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}

async function stop() {
  try { await api('/threads/' + thread.value.id + '/stop', {}); await load() }
  catch (cause) { error.value = cause.message }
}

async function preview(draft) {
  try { selectedDraft.value = await api('/drafts/' + draft.id) }
  catch (cause) { error.value = cause.message }
}

async function useDraft(draft) {
  try {
    if (thread.value.task_id) {
      const response=await fetch(`/api/task-versions/tasks/${thread.value.task_id}/drafts/${draft.id}`,{method:'POST',credentials:'include'})
      const result=await response.json();if(!response.ok)throw new Error(result.detail||'更新失败')
      await load();return
    }
    const value = await api('/drafts/' + draft.id)
    emit('use-draft', { ...value, task_id: thread.value.task_id })
  } catch (cause) { error.value = cause.message }
}

onMounted(async () => {
  try {
    setting.value = await api('/settings')
    threads.value = await api('/threads')
    if (props.taskId) await newThread()
    else {
      const previous = threads.value.find(item => !item.task_id)
      if (previous) await load(previous.id)
      else await newThread()
    }
    timer = window.setInterval(async () => {
      if (!alive || !thread.value || busy.value) return
      try { await load() } catch (cause) { if (alive) error.value = cause.message }
    }, 1800)
  } catch (cause) { error.value = cause.message }
})
onBeforeUnmount(() => { alive = false; generation++; window.clearInterval(timer) })
</script>

<template>
  <div class="modal-layer ai-layer" @mousedown.self="emit('close')">
    <section class="ai-dialog" role="dialog" aria-modal="true" aria-labelledby="ai-title">
      <header class="ai-heading">
        <h2 id="ai-title">{{ taskId ? 'AI 助手' : 'AI 创建任务' }}</h2>
        <button class="modal-close" aria-label="关闭 AI 助手" @click="emit('close')">×</button>
      </header>
      <p v-if="error" class="ai-error" role="alert">{{ error }}</p>
      <p v-if="setting && !setting.key_configured" class="ai-error">请联系超级管理员配置 AI 模型。</p>
      <div class="ai-layout">
        <main class="ai-chat">
          <div v-if="!taskId" class="ai-history">
            <select aria-label="选择已有 AI 对话" :disabled="busy" :value="thread?.id" @change="selectThread(Number($event.target.value))">
              <option v-for="item in threads" :key="item.id" :value="item.id">{{ item.title }}</option>
            </select>
            <button class="button ghost" :disabled="busy" @click="newThread">新建对话</button>
          </div>
          <div class="ai-messages">
            <div v-if="!thread?.messages?.length" class="ai-intro"><h3>想让任务做什么？</h3><p>发一个网址，或描述表格处理规则。</p></div>
            <article v-for="item in thread?.messages || []" :key="item.id" class="ai-message" :class="item.role">
              <strong>{{ item.role === 'user' ? '我的需求' : 'AI 助手' }}</strong>
              <details v-if="item.role !== 'user' && item.content.length > 300" class="ai-message-more"><summary>{{ item.content.slice(0, 180) }}… <small>展开全文</small></summary><div>{{ item.content }}</div></details>
              <div v-else>{{ item.content }}</div>
            </article>
          </div>
          <div v-if="latest" class="ai-status" aria-live="polite"><span>{{ labels[latest.status] }}{{ active?.stop_requested ? ' · 正在停止' : '' }}</span></div>
          <p v-if="latest?.error" class="ai-error">{{ latest.error }}</p>
          <form class="ai-compose" @submit.prevent="send">
            <textarea v-model="message" rows="3" maxlength="12000" aria-label="任务需求" placeholder="描述需求或要修改的地方…" :disabled="!thread"></textarea>
            <div><small>DeepSeek</small><button v-if="active" class="button ghost" type="button" :disabled="active.stop_requested" @click="stop">停止</button><button class="button primary" type="submit" :disabled="!thread || busy || Boolean(active) || !message.trim() || !setting?.key_configured">发送</button></div>
          </form>
        </main>
        <aside class="ai-side">
          <h3>{{ showHistory ? '草稿版本' : '最新草稿' }}</h3>
          <article v-for="draft in visibleDrafts" :key="draft.id" class="ai-draft">
            <strong>V{{ draft.version }} · {{ draft.name }}</strong><div class="ai-draft-status"><span class="mini-badge neutral-badge">{{ draft.trial ? ({ pending: '等待试跑', running: '正在采集', success: '试跑通过', failed: '试跑未通过', cancelled: '已停止', interrupted: '已中断' }[draft.trial.status]) : '语法通过 · 未试运行' }}</span></div>
            <p v-if="draft.trial?.message">{{ draft.trial.message }}</p>
            <div v-if="draft.trial?.files?.length" class="ai-links"><a v-for="file in draft.trial.files" :key="file" :href="`/api/ai/collection-trials/${draft.trial.id}/files/${encodeURIComponent(file)}`">{{ file }}</a></div>
            <details v-if="draft.trial?.log" class="ai-disclosure"><summary>试跑日志</summary><pre class="ai-trial-log">{{ draft.trial.log }}</pre></details>
            <details v-if="draft.description" class="ai-disclosure"><summary>草稿说明</summary><p>{{ draft.description }}</p></details>
            <div class="ai-draft-actions"><button class="button ghost" @click="preview(draft)">查看代码</button><button class="button primary" :disabled="Boolean(active)" @click="useDraft(draft)">{{ thread?.task_id ? '更新本任务' : '使用草稿' }}</button></div>
            <div class="ai-links"><a :href="`/api/ai/drafts/${draft.id}/download`">下载 Python</a><a :href="`/api/ai/drafts/${draft.id}/download?file=requirements.txt`">下载依赖</a></div>
          </article>
          <p v-if="!thread?.drafts?.length" class="ai-note">暂无草稿</p>
          <button v-if="thread?.drafts?.length > 1" class="button ghost" :aria-expanded="showHistory" @click="showHistory = !showHistory">{{ showHistory ? '收起历史' : `历史草稿（${thread.drafts.length - 1}）` }}</button>
          <details class="ai-disclosure ai-records"><summary>处理记录与用量</summary>
            <p class="ai-note">{{ setting?.model }} · {{ tokens.toLocaleString() }} tokens</p>
            <ol v-if="thread?.events?.length" class="ai-events"><li v-for="item in thread.events" :key="item.id"><span>{{ item.message }}</span><small>{{ new Date(item.created_at).toLocaleTimeString('zh-CN') }}</small></li></ol>
            <p v-else class="ai-note">暂无记录</p>
          </details>
        </aside>
      </div>
      <div v-if="selectedDraft" class="ai-code"><header><strong>V{{ selectedDraft.version }} · main.py</strong><button class="button ghost" @click="selectedDraft = null">收起代码</button></header><pre>{{ selectedDraft.source }}</pre><p>依赖：{{ selectedDraft.requirements || '仅标准库' }}</p></div>
      <AiMaintenance v-if="taskId" :key="taskId" :task-id="taskId" @open-execution="id => emit('open-execution', id)" />
    </section>
  </div>
</template>

<style scoped>
.ai-trial-log{white-space:pre-wrap;overflow-wrap:anywhere;max-height:220px;overflow:auto;font-size:12px}
.ai-heading{align-items:center}.ai-intro{text-align:center;display:grid;align-content:center;height:100%;box-sizing:border-box}.ai-intro h3{margin:0;font-size:19px;font-weight:500}.ai-intro p{margin:10px 0;font-size:13px}.ai-disclosure{font-size:12px;color:#60746a;line-height:1.7}.ai-disclosure summary{cursor:pointer;padding:7px 0}.ai-records{margin-top:18px;border-top:1px solid #e1e8e3;padding-top:8px}.ai-draft-status{margin-top:10px}
.ai-layer{z-index:60;padding:24px}.ai-dialog{background:#fff;border-radius:20px;width:min(1220px,96vw);max-height:94vh;overflow:auto;box-shadow:0 30px 100px #183b3433}.ai-heading{padding:25px 30px;display:flex;justify-content:space-between;border-bottom:1px solid #e6ebe8}.ai-heading h2{margin:7px 0}.ai-heading p,.ai-note{color:#687c75;font-size:13px;line-height:1.7}.ai-layout{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(300px,1fr)}.ai-chat{padding:20px 26px;min-width:0}.ai-side{background:#f7f9f7;padding:20px;border-left:1px solid #e6ebe8;min-width:0}.ai-side h3{font-size:14px}.ai-history{display:flex;gap:10px}.ai-history select{min-width:0;flex:1;border:1px solid #dce5df;border-radius:8px;padding:8px}.ai-messages{height:39vh;min-height:220px;overflow:auto;padding:15px 0}.ai-message{padding:14px 16px;border:1px solid #e4ebe7;border-radius:12px;margin:0 0 14px;overflow-wrap:anywhere}.ai-message.user{background:#edf5f0;margin-left:30px}.ai-message strong{display:block;font-size:12px;color:#487462;margin-bottom:8px}.ai-message div{white-space:pre-wrap;font-size:14px;line-height:1.8}.ai-intro{padding:24px 10px;color:#60736a;line-height:1.8}.ai-status{display:flex;justify-content:space-between;font-size:12px;gap:10px;color:#557163;padding:12px 0}.ai-compose textarea{width:100%;resize:vertical;border:1px solid #d9e3dd;border-radius:12px;padding:12px;font:inherit;box-sizing:border-box}.ai-compose>div{display:flex;gap:10px;align-items:center;margin-top:10px}.ai-compose small{flex:1;color:#6e7b73}.ai-error{color:#a53e32;background:#fff2ef;padding:12px 18px;border-radius:8px;white-space:pre-wrap;font-size:13px}.ai-draft{background:white;border:1px solid #dfe8e0;padding:15px;border-radius:12px;margin-bottom:12px}.ai-draft p{font-size:13px;color:#61736b;line-height:1.6}.ai-draft-actions{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.ai-links{display:flex;gap:16px;font-size:12px}.ai-links a{color:#287251}.ai-events{list-style:none;padding:0;max-height:250px;overflow:auto;font-size:12px}.ai-events li{padding:10px 0;border-bottom:1px solid #e3e9e4;display:flex;gap:8px;justify-content:space-between}.ai-events small{color:#819087;white-space:nowrap}.ai-code{padding:20px 26px;border-top:1px solid #e1e9e3}.ai-code header{display:flex;justify-content:space-between;align-items:center}.ai-code pre{max-height:45vh;overflow:auto;background:#162a22;color:#e1eee5;padding:18px;border-radius:12px;font-size:12px;line-height:1.6}.ai-code p{font-size:12px;white-space:pre-wrap}@media(max-width:760px){.ai-layer{padding:8px}.ai-dialog{width:98vw}.ai-layout{grid-template-columns:1fr}.ai-heading{padding:18px}.ai-chat{padding:16px}.ai-side{border-left:none;border-top:1px solid #e6ebe8}.ai-messages{height:34vh}.ai-status{flex-wrap:wrap}.ai-compose>div{flex-wrap:wrap}.ai-compose small{flex-basis:100%}}
</style>
