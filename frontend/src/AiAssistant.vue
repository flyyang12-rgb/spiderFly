<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AiMaintenance from './AiMaintenance.vue'
import AiThreadPicker from './AiThreadPicker.vue'
import AiMessageContent from './AiMessageContent.vue'
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
const composer = ref(null)
const dialog = ref(null)
const codePanel = ref(null)
const outputPanel = ref(null)
const outputToggle = ref(null)
const showOutput = ref(false)
const messageList = ref(null)
const nearBottom = ref(true)
const hasBrowser = computed(() => thread.value?.browser && (thread.value.browser.status !== 'closed' || thread.value.browser.row_count))
const hasOutput = computed(() => Boolean(thread.value?.drafts?.length || hasBrowser.value))
let previousFocus = null
let previousOverflow = ''
const emptyThread = computed(() => Boolean(thread.value) && !thread.value.task_id && !thread.value.messages?.length && !thread.value.turns?.length && !thread.value.drafts?.length && (!thread.value.browser || (thread.value.browser.status === 'closed' && !thread.value.browser.row_count && !thread.value.browser.message)))
const visibleDrafts = computed(() => showHistory.value ? thread.value?.drafts || [] : (thread.value?.drafts || []).slice(0, 1))
let timer
let alive = true
let generation = 0
const active = computed(() => thread.value?.turns?.find(item => ['pending', 'running'].includes(item.status)))
const latest = computed(() => thread.value?.turns?.[0])
const turnError = computed(() => latest.value?.status === 'cancelled' && latest.value.error === '已停止 AI 处理，现有对话与草稿保留。' ? '' : latest.value?.error)
const tokens = computed(() => (thread.value?.turns || []).reduce((sum, item) => sum + item.input_tokens + item.output_tokens, 0))
const labels = { pending: '等待 AI 处理', running: 'AI 正在处理', waiting_user: '等待你处理浏览器', completed: '本轮完成', failed: '本轮未完成', cancelled: '已停止', interrupted: '已中断' }

function keyboard(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    if (selectedDraft.value) selectedDraft.value = null
    else if (showOutput.value) showOutput.value = false
    else emit('close')
    return
  }
  if (event.key !== 'Tab') return
  const region = selectedDraft.value ? codePanel.value : dialog.value
  const controls = [...region.querySelectorAll('button:not(:disabled), a[href], textarea:not(:disabled), summary, [tabindex="0"]')]
    .filter(element => element.getClientRects().length)
  const first = controls[0], last = controls.at(-1)
  if (!first) { event.preventDefault(); region.focus(); return }
  if (event.shiftKey && (document.activeElement === first || document.activeElement === region)) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}

watch(selectedDraft, async value => {
  await nextTick()
  if (!alive) return
  if (value) codePanel.value?.focus()
  else if (showOutput.value) outputPanel.value?.focus()
  else composer.value?.focus()
})

watch(showOutput, async value => {
  await nextTick()
  if (!alive) return
  if (value) outputPanel.value?.focus()
  else outputToggle.value?.focus()
})

function trackScroll() {
  const element = messageList.value
  if (element) nearBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 80
}

watch(() => [thread.value?.id, thread.value?.messages?.at(-1)?.id, thread.value?.messages?.at(-1)?.content], async (value, old) => {
  if (value[0] !== old?.[0]) { nearBottom.value = true; showOutput.value = false }
  if (!nearBottom.value) return
  await nextTick()
  if (alive && messageList.value) messageList.value.scrollTop = messageList.value.scrollHeight
})

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
  if (alive && token === generation) {
    thread.value = value
    threads.value = threads.value.map(item => item.id === value.id ? { ...item, title: value.title } : item)
  }
}

async function newThread() {
  if (busy.value) return
  if (emptyThread.value) return
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
  if (busy.value || id === thread.value?.id) return
  busy.value = true
  try { selectedDraft.value = null; showHistory.value = false; await load(id) }
  catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}

async function deleteThread(id) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await api('/threads/' + id, undefined, 'DELETE')
    threads.value = threads.value.filter(item => item.id !== id)
    if (thread.value?.id === id) {
      generation++
      thread.value = null
      selectedDraft.value = null
      showHistory.value = false
      message.value = ''
      const next = threads.value.find(item => !item.task_id) || threads.value[0]
      if (next) await load(next.id)
      else {
        const created = await api('/threads', {})
        await load(created.id)
        threads.value = await api('/threads')
      }
    }
  } catch (cause) { error.value = cause.message }
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

async function browserAction(action) {
  busy.value = true
  error.value = ''
  try { await api('/threads/' + thread.value.id + '/browser/' + action, {}); await load() }
  catch (cause) { error.value = cause.message }
  finally { busy.value = false }
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
  previousFocus = document.activeElement
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  await nextTick()
  dialog.value?.focus()
  try {
    setting.value = await api('/settings')
    threads.value = await api('/threads')
    if (props.taskId) await newThread()
    else {
      const previous = threads.value.find(item => !item.task_id)
      if (previous) await load(previous.id)
      else await newThread()
    }
    if (!alive) return
    if (document.activeElement === dialog.value) composer.value?.focus()
    timer = window.setInterval(async () => {
      if (!alive || !thread.value || busy.value) return
      try { await load() } catch (cause) { if (alive) error.value = cause.message }
    }, 1800)
  } catch (cause) { error.value = cause.message }
})
onBeforeUnmount(() => { alive = false; generation++; window.clearInterval(timer); document.body.style.overflow = previousOverflow; if (previousFocus?.isConnected) previousFocus.focus() })
</script>

<template>
  <div class="modal-layer ai-layer" @mousedown.self="emit('close')">
    <section ref="dialog" class="ai-dialog" role="dialog" aria-modal="true" aria-labelledby="ai-title" tabindex="-1" @keydown="keyboard">
      <header class="ai-heading">
        <h2 id="ai-title">AI 助手</h2>
        <div v-if="!taskId" class="ai-history">
          <AiThreadPicker :threads="threads" :selected-id="thread?.id" :current-title="emptyThread ? '新对话' : thread?.title" :disabled="busy" @select="selectThread" @delete="deleteThread" />
          <button class="ai-new-thread" type="button" :disabled="busy" @click="newThread"><span aria-hidden="true">＋</span>新对话</button>
        </div>
        <button v-if="hasOutput" ref="outputToggle" class="ai-output-toggle" type="button" :aria-expanded="showOutput" aria-controls="ai-output-panel" @click="showOutput = !showOutput">{{ showOutput ? '收起结果' : thread?.browser?.status === 'waiting_user' ? '需要处理' : '草稿与结果' }}</button>
        <button class="modal-close" type="button" aria-label="关闭 AI 助手" @click="emit('close')">×</button>
      </header>
      <p v-if="error" class="ai-error" role="alert">{{ error }}</p>
      <p v-if="setting && !setting.key_configured" class="ai-error" role="alert">请先在管理中心配置 AI 模型。</p>
      <div class="ai-layout" :class="{ 'has-output': hasOutput && showOutput, 'show-output': showOutput }" :inert="Boolean(selectedDraft)">
        <main class="ai-chat">
          <div ref="messageList" class="ai-messages" aria-label="对话内容" @scroll="trackScroll">
            <div v-if="!thread?.messages?.length" class="ai-intro"><h3>想让任务做什么？</h3></div>
            <article v-for="item in thread?.messages || []" :key="item.id" class="ai-message" :class="item.role" :aria-label="item.role === 'user' ? '我的消息' : 'AI 回复'">
              <span class="ai-message-author">{{ item.role === 'user' ? '你' : 'AI 助手' }}</span>
              <div v-if="item.role === 'user'" class="ai-user-content">{{ item.content }}</div>
              <AiMessageContent v-else :content="item.content" />
            </article>
            <details v-if="thread?.events?.length || tokens" class="ai-disclosure ai-records">
              <summary>处理记录</summary>
              <p class="ai-note">{{ setting?.model }} · {{ tokens.toLocaleString() }} tokens</p>
              <ol v-if="thread?.events?.length" class="ai-events"><li v-for="item in thread.events" :key="item.id"><span>{{ item.message }}</span><time>{{ new Date(item.created_at).toLocaleTimeString('zh-CN') }}</time></li></ol>
            </details>
          </div>
          <p v-if="turnError && turnError !== error" class="ai-turn-error" role="alert">{{ turnError }}</p>
          <form class="ai-compose" @submit.prevent="send">
            <textarea ref="composer" v-model="message" rows="2" maxlength="12000" aria-label="任务需求" placeholder="描述任务或继续提问…" :disabled="!thread"></textarea>
            <div class="ai-compose-footer">
              <span v-if="latest && latest.status !== 'completed'" class="ai-status" aria-live="polite"><i :class="{ active: Boolean(active) }" aria-hidden="true"></i>{{ active?.stop_requested ? '正在停止…' : labels[latest.status] }}</span>
              <button v-if="active" class="button secondary ai-send" type="button" :disabled="active.stop_requested" @click="stop">停止</button>
              <button v-else class="button primary ai-send" type="submit" :disabled="!thread || busy || !message.trim() || !setting?.key_configured">发送 <span aria-hidden="true">↑</span></button>
            </div>
          </form>
        </main>
        <aside v-if="hasOutput" id="ai-output-panel" ref="outputPanel" class="ai-side" :class="{ 'mobile-open': showOutput }" aria-label="草稿与探索结果" tabindex="-1">
          <section v-if="hasBrowser" class="ai-browser" aria-label="探索结果">
            <header class="ai-section-heading"><h3>探索结果</h3><span :class="{ waiting: thread.browser.status === 'waiting_user' }">{{ ({ closed: '已结束', native: '采集中', open: '浏览器已打开', waiting_user: '需要处理' })[thread.browser.status] || '探索中' }}</span></header>
            <div v-if="thread.browser.row_count" class="ai-browser-result"><span><strong>{{ thread.browser.row_count }}</strong> 条数据</span><a :href="`/api/ai/threads/${thread.id}/browser/results.csv`">下载 CSV</a></div>
            <p v-if="thread.browser.status === 'waiting_user'" class="ai-browser-prompt">请在主控电脑完成浏览器操作后继续。</p>
            <details :key="thread.id + ':' + thread.browser.status" class="ai-disclosure" :open="thread.browser.status === 'waiting_user'">
              <summary>探索详情</summary>
              <p v-if="thread.browser.message">{{ thread.browser.message }}</p>
              <p v-if="thread.browser.url" class="ai-browser-url">{{ thread.browser.url }}</p>
            </details>
            <div v-if="['open', 'waiting_user', 'native'].includes(thread.browser.status)" class="ai-draft-actions">
              <button v-if="thread.browser.status === 'waiting_user'" class="button primary" :disabled="busy || Boolean(active)" @click="browserAction('resume')">继续</button>
              <button class="button ghost" :disabled="busy" @click="browserAction('close')">{{ thread.browser.status === 'native' ? '结束采集' : '关闭浏览器' }}</button>
            </div>
          </section>
          <template v-if="thread?.drafts?.length">
            <header class="ai-section-heading"><h3>{{ showHistory ? '草稿版本' : '最新草稿' }}</h3><span v-if="thread.drafts.length > 1">{{ thread.drafts.length }} 个版本</span></header>
            <article v-for="draft in visibleDrafts" :key="draft.id" class="ai-draft">
              <div class="ai-draft-title"><strong>{{ draft.name }}</strong><span>v{{ draft.version }}</span></div>
              <span class="ai-draft-status" :class="{ verified: draft.trial?.status === 'success' }">{{ draft.trial ? ({ pending: '等待试跑', running: '正在试跑', success: '试跑通过', failed: '试跑未通过', cancelled: '已停止', interrupted: '已中断' }[draft.trial.status]) : '未试运行' }}</span>
              <p v-if="draft.trial?.message" class="ai-trial-message">{{ draft.trial.message }}</p>
              <div v-if="draft.trial?.files?.length" class="ai-links"><a v-for="file in draft.trial.files" :key="file" :href="`/api/ai/collection-trials/${draft.trial.id}/files/${encodeURIComponent(file)}`">{{ file }}</a></div>
              <details v-if="draft.trial?.log" class="ai-disclosure"><summary>试跑日志</summary><pre class="ai-trial-log">{{ draft.trial.log }}</pre></details>
              <details v-if="draft.description" class="ai-disclosure"><summary>草稿说明</summary><p>{{ draft.description }}</p></details>
              <div class="ai-draft-actions"><button class="button secondary" :disabled="Boolean(active)" @click="useDraft(draft)">{{ thread?.task_id ? '提交并验证' : '使用草稿' }}</button><button class="button ghost" @click="preview(draft)">查看代码</button></div>
              <details class="ai-disclosure ai-downloads"><summary>下载文件</summary><div class="ai-links"><a :href="`/api/ai/drafts/${draft.id}/download`">Python 脚本</a><a :href="`/api/ai/drafts/${draft.id}/download?file=requirements.txt`">依赖清单</a></div></details>
            </article>
            <button v-if="thread.drafts.length > 1" class="button ghost ai-history-toggle" :aria-expanded="showHistory" @click="showHistory = !showHistory">{{ showHistory ? '收起历史' : `历史草稿（${thread.drafts.length - 1}）` }}</button>
          </template>
        </aside>
      </div>
      <section v-if="selectedDraft" ref="codePanel" class="ai-code" aria-label="代码预览" tabindex="-1"><header><strong>v{{ selectedDraft.version }} · main.py</strong><button class="button secondary" @click="selectedDraft = null">收起代码</button></header><pre tabindex="0">{{ selectedDraft.source }}</pre><p>依赖：{{ selectedDraft.requirements || '仅标准库' }}</p></section>
      <AiMaintenance v-if="taskId" :key="taskId" :task-id="taskId" @open-execution="id => emit('open-execution', id)" />
    </section>
  </div>
</template>

<style scoped>
.ai-layer{z-index:60;padding:24px;background:rgb(25 31 27 / 32%);backdrop-filter:blur(3px)}
.ai-dialog{position:relative;display:flex;flex-direction:column;width:min(1120px,100%);height:min(780px,calc(100dvh - 48px));max-height:calc(100dvh - 48px);overflow:hidden;border:1px solid var(--border,#dfe3df);border-radius:14px;background:var(--surface,#fff);box-shadow:0 20px 64px rgb(20 30 23 / 16%);outline:none;animation:ai-enter 160ms ease-out}
.ai-heading{display:flex;align-items:center;gap:24px;flex:none;min-height:68px;padding:14px 24px;border-bottom:1px solid var(--border-soft,#ecefec)}
.ai-heading h2{flex:none;margin:0;font-size:17px;line-height:26px;font-weight:600;color:var(--ink,#221814)}
.ai-output-toggle{display:block;flex:none;padding:6px 8px;border:1px solid var(--border,#dfe3df);border-radius:8px;background:#fff;font:inherit;font-size:12px;white-space:nowrap;cursor:pointer}
.ai-heading .modal-close{display:grid;place-items:center;flex:none;margin-left:auto;width:32px;height:32px;border:0;border-radius:8px;background:transparent;color:#68716b;font-size:23px;line-height:1;cursor:pointer}.ai-heading .modal-close:hover{background:var(--surface-subtle,#f7f8f6);color:var(--ink,#221814)}
.ai-history{display:flex;align-items:center;gap:8px;min-width:0;max-width:480px;margin-left:auto;flex:1}.ai-history :deep(.thread-picker){flex:1;min-width:0}.ai-history :deep(.thread-trigger){border-color:transparent;background:transparent;font-size:13px}.ai-history :deep(.thread-trigger:hover){background:var(--surface-subtle,#f7f8f6)}
.ai-new-thread{display:flex;flex:none;align-items:center;gap:4px;height:36px;padding:0 10px;border:0;border-radius:8px;background:transparent;color:#515c54;font-size:13px;font-weight:500;white-space:nowrap;cursor:pointer;transition:background 140ms ease,color 140ms ease}.ai-new-thread span{font-size:18px}.ai-new-thread:hover:not(:disabled){background:var(--surface-subtle,#f7f8f6);color:var(--ink,#221814)}.ai-new-thread:disabled{opacity:.5;cursor:not-allowed}
.ai-layout{display:grid;grid-template-columns:minmax(0,1fr);flex:1;min-height:0}.ai-layout.has-output{grid-template-columns:minmax(0,1fr) 300px}
.ai-chat{display:flex;flex-direction:column;min-width:0;min-height:0;padding:0 28px 24px;background:#fff}
.ai-messages{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;padding:28px 8px 16px 0;scrollbar-width:thin;scrollbar-color:#c7cfc9 transparent}
.ai-message{max-width:720px;margin:0 auto 28px;overflow-wrap:anywhere}.ai-message-author{display:block;margin-bottom:8px;color:#68716b;font-size:12px;line-height:18px;font-weight:500}.ai-message.assistant{padding:0 4px}.ai-message.user{width:fit-content;max-width:85%;margin-left:auto;margin-right:0;padding:12px 16px;border-radius:12px;background:#f4f6f3}.ai-message.user .ai-message-author{display:none}.ai-user-content{color:var(--text,#292b28);font-size:14px;line-height:1.7;white-space:pre-wrap}
.ai-intro{display:grid;place-content:center;min-height:100%;padding:32px 0}.ai-intro h3{margin:0;color:var(--ink,#221814);font-size:24px;font-weight:500;line-height:1.5;letter-spacing:-.02em}
.ai-compose{flex:none;margin:0 auto;width:100%;max-width:760px;padding:12px;border:1px solid #d9dfd9;border-radius:12px;background:#fff;transition:border-color 140ms ease,box-shadow 140ms ease}.ai-compose:focus-within{border-color:var(--brand,#009139);box-shadow:0 0 0 2px rgb(0 145 57 / 7%)}
.ai-compose textarea{display:block;width:100%;height:64px;min-height:56px;max-height:160px;resize:vertical;padding:4px;border:0;outline:none;background:transparent;color:var(--text,#292b28);font:inherit;font-size:14px;line-height:1.7}.ai-compose textarea::placeholder{color:#828a83}.ai-compose-footer{display:flex;align-items:center;justify-content:flex-end;gap:12px;min-height:36px;margin-top:4px}.ai-send{flex:none;min-width:76px;min-height:36px;font-size:13px;border-radius:8px}.ai-send span{margin-left:4px;font-size:17px;font-weight:400}
.ai-status{display:flex;align-items:center;gap:8px;margin-right:auto;color:#5e6961;font-size:12px;line-height:18px}.ai-status i{width:6px;height:6px;border-radius:50%;background:#879389;flex:none}.ai-status i.active{background:var(--brand,#009139);animation:ai-pulse 1.6s ease-in-out infinite}
.ai-error,.ai-turn-error{margin:12px 24px 0;padding:10px 12px;border:1px solid #f2d4c9;border-radius:8px;background:#fff7f3;color:#a13a2d;font-size:13px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere;max-height:100px;overflow:auto;flex:none}.ai-turn-error{margin:0 0 12px}
.ai-side{display:none;min-width:0;min-height:0;overflow:auto;overscroll-behavior:contain;padding:24px 20px;border-left:1px solid var(--border-soft,#ecefec);background:#fafbf9;scrollbar-width:thin}.ai-side.mobile-open{display:block}
.ai-section-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:16px}.ai-section-heading h3{margin:0;font-size:13px;line-height:20px;font-weight:600;color:#435147}.ai-section-heading>span{flex:none;color:#758078;font-size:12px;line-height:20px}.ai-section-heading .waiting{color:#9a5a15}
.ai-draft{padding:0 0 20px;margin-bottom:20px;border-bottom:1px solid var(--border-soft,#ecefec)}.ai-draft:last-of-type{margin-bottom:0;border-bottom:0}.ai-draft-title{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:8px;overflow-wrap:anywhere}.ai-draft-title strong{min-width:0;color:var(--ink,#221814);font-size:15px;line-height:1.5;font-weight:600}.ai-draft-title>span{flex:none;color:#758078;font-size:12px;line-height:22px}.ai-draft-status{display:inline-block;margin-bottom:12px;color:#69756c;font-size:12px;line-height:20px}.ai-draft-status.verified{color:var(--brand-strong,#00752e)}
.ai-draft-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}.ai-draft-actions .button{min-height:34px;padding:6px 10px;font-size:12px;font-weight:500}.ai-history-toggle{font-size:12px}
.ai-disclosure{font-size:13px;line-height:1.7;color:#637067;overflow-wrap:anywhere}.ai-disclosure>summary{width:fit-content;padding:7px 0;cursor:pointer}.ai-disclosure>p{margin:4px 0 12px;white-space:pre-wrap;color:#59665d;font-size:13px;line-height:1.7}.ai-downloads{margin-top:8px}.ai-links{display:flex;flex-wrap:wrap;gap:8px 16px;padding:4px 0;font-size:12px;overflow-wrap:anywhere}.ai-links a,.ai-browser-result a{color:var(--brand-strong,#00752e);text-underline-offset:3px}.ai-trial-message{font-size:13px;line-height:1.7;color:#59665d;white-space:pre-wrap;overflow-wrap:anywhere}
.ai-records{max-width:720px;margin:0 auto 8px;color:#6b766e}.ai-records>summary{font-size:12px}.ai-note{font-size:12px;line-height:1.7;color:#6b766e;margin:6px 0}.ai-events{padding:0;margin:8px 0;list-style:none;font-size:12px}.ai-events li{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;padding:8px 0;border-bottom:1px solid var(--border-soft,#ecefec)}.ai-events time{flex:none;color:#7d867f;font-size:11px;line-height:20px}
.ai-browser{padding-bottom:20px;margin-bottom:24px;border-bottom:1px solid var(--border,#dfe3df);font-size:13px;line-height:1.7}.ai-browser-result{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:16px 0}.ai-browser-result strong{font-size:24px;font-weight:600;color:var(--ink,#221814)}.ai-browser-result a{font-size:12px}.ai-browser-prompt{color:#8e5a24}.ai-browser-url{overflow-wrap:anywhere}
.ai-trial-log{max-height:220px;overflow:auto;padding:12px;border:1px solid var(--border-soft,#ecefec);border-radius:8px;background:#f4f6f3;color:#3b473e;font-size:12px;line-height:1.65;white-space:pre-wrap}
.ai-code{position:absolute;inset:68px 0 0;display:flex;flex-direction:column;min-height:0;padding:20px 24px;background:#fff;outline:none;z-index:2}.ai-code>header{display:flex;justify-content:space-between;align-items:center;gap:16px;flex:none;font-size:14px}.ai-code pre{flex:1;min-height:0;overflow:auto;padding:20px;margin:16px 0;border:1px solid var(--border-soft,#ecefec);border-radius:8px;background:#f7f8f6;color:#29362d;font-size:13px;line-height:1.7;white-space:pre;tab-size:4}.ai-code p{font-size:12px;color:#657269;white-space:pre-wrap;overflow-wrap:anywhere}
.ai-dialog :deep(.maintenance-panel){flex:none;max-height:30%;overflow:auto;padding:12px 24px;background:#fafbf9}.ai-dialog :is(button,textarea,summary,[tabindex]):focus-visible{outline:2px solid var(--brand,#009139);outline-offset:3px}.ai-dialog .ai-compose textarea:focus-visible{outline:none}
@keyframes ai-enter{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}@keyframes ai-pulse{50%{opacity:.4}}
@media(prefers-reduced-motion:reduce){.ai-dialog,.ai-status i.active{animation:none}.ai-compose,.ai-new-thread{transition:none}}
@media(max-width:760px){.ai-layer{padding:8px}.ai-dialog{width:100%;height:calc(100dvh - 16px);max-height:calc(100dvh - 16px);border-radius:12px}.ai-heading{flex-wrap:wrap;gap:8px 12px;padding:12px 16px}.ai-heading h2{font-size:16px;flex:1}.ai-heading .modal-close{order:1}.ai-history{order:2;flex-basis:100%;max-width:none;margin:0}.ai-output-toggle{display:block;padding:6px 8px;border:1px solid var(--border,#dfe3df);border-radius:8px;background:#fff;font:inherit;font-size:12px;cursor:pointer}.ai-layout.has-output{display:grid;grid-template-columns:minmax(0,1fr);position:relative;overflow:hidden}.ai-chat{padding:0 16px 16px;flex:1}.has-output .ai-chat{min-height:0;height:auto;flex:1}.show-output .ai-chat{visibility:hidden}.ai-messages{padding-top:24px}.ai-message{margin-bottom:24px}.ai-message.user{max-width:92%;padding:10px 12px}.ai-side{display:none;position:absolute;inset:0;overflow:auto;border-left:0;padding:20px 16px;outline:none}.ai-side.mobile-open{display:block}.ai-code{inset:0;padding:16px}.ai-intro h3{font-size:22px}.ai-error{margin:8px 16px 0}.ai-draft-actions .button{font-size:13px}.ai-dialog :deep(.maintenance-panel){padding:12px 16px}.ai-status{font-size:11px}}
@media(max-height:540px) and (min-width:761px){.ai-layer{padding:8px}.ai-dialog{height:calc(100dvh - 16px);max-height:calc(100dvh - 16px)}.ai-heading{min-height:54px;padding:8px 20px}.ai-compose textarea{height:40px;min-height:40px}.ai-chat{padding-bottom:12px}.ai-messages{padding-top:16px}}
</style>
