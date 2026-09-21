<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AiMaintenance from './AiMaintenance.vue'
import AiThreadPicker from './AiThreadPicker.vue'
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
const emptyThread = computed(() => Boolean(thread.value) && !thread.value.task_id && !thread.value.messages?.length && !thread.value.turns?.length && !thread.value.drafts?.length && (!thread.value.browser || (thread.value.browser.status === 'closed' && !thread.value.browser.row_count && !thread.value.browser.message)))
const visibleDrafts = computed(() => showHistory.value ? thread.value?.drafts || [] : (thread.value?.drafts || []).slice(0, 1))
let timer
let alive = true
let generation = 0
const active = computed(() => thread.value?.turns?.find(item => ['pending', 'running'].includes(item.status)))
const latest = computed(() => thread.value?.turns?.[0])
const tokens = computed(() => (thread.value?.turns || []).reduce((sum, item) => sum + item.input_tokens + item.output_tokens, 0))
const labels = { pending: '等待 AI 处理', running: 'AI 正在处理', waiting_user: '等待你处理浏览器', completed: '本轮完成', failed: '本轮未完成', cancelled: '已停止', interrupted: '已中断' }

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
        <div class="ai-heading-copy">
          <span class="ai-eyebrow">{{ taskId ? 'TASK COPILOT' : 'TASK STUDIO' }}</span>
          <h2 id="ai-title">{{ taskId ? 'AI 助手' : 'AI 创建任务' }}</h2>
          <p>{{ taskId ? '继续完善需求、代码和任务版本' : '把业务需求整理成可以运行的 Python 任务' }}</p>
        </div>
        <button class="modal-close" aria-label="关闭 AI 助手" @click="emit('close')">×</button>
      </header>
      <p v-if="error" class="ai-error" role="alert">{{ error }}</p>
      <p v-if="setting && !setting.key_configured" class="ai-error">请联系超级管理员配置 AI 模型。</p>
      <div class="ai-layout">
        <main class="ai-chat">
          <div v-if="!taskId" class="ai-history">
            <AiThreadPicker :threads="threads" :selected-id="thread?.id" :current-title="emptyThread ? '新对话' : thread?.title" :disabled="busy" @select="selectThread" @delete="deleteThread" />
            <button class="ai-new-thread" type="button" :disabled="busy" @click="newThread"><span aria-hidden="true">＋</span>新建对话</button>
          </div>
          <div class="ai-messages">
            <div v-if="!thread?.messages?.length" class="ai-intro"><h3>想让任务做什么？</h3><p>说出目标网站和采集条件，或描述表格处理规则。</p></div>
            <article v-for="item in thread?.messages || []" :key="item.id" class="ai-message" :class="item.role">
              <header class="ai-message-meta"><span>{{ item.role === 'user' ? '我' : 'AI' }}</span><strong>{{ item.role === 'user' ? '我的需求' : 'AI 助手' }}</strong></header>
              <div>{{ item.content }}</div>
            </article>
          </div>
          <div v-if="latest" class="ai-status" aria-live="polite"><i :class="{ active: Boolean(active) }"></i><span>{{ labels[latest.status] }}{{ active?.stop_requested ? ' · 正在停止' : '' }}</span></div>
          <p v-if="latest?.error" class="ai-error">{{ latest.error }}</p>
          <form class="ai-compose" @submit.prevent="send">
            <textarea ref="composer" v-model="message" rows="3" maxlength="12000" aria-label="任务需求" placeholder="描述需求或要修改的地方…" :disabled="!thread"></textarea>
            <div><small><i></i>DeepSeek</small><button v-if="active" class="button ghost" type="button" :disabled="active.stop_requested" @click="stop">停止</button><button class="button primary ai-send" type="submit" :disabled="!thread || busy || Boolean(active) || !message.trim() || !setting?.key_configured">发送</button></div>
          </form>
        </main>
        <aside class="ai-side">
          <section v-if="thread?.browser && (thread.browser.status !== 'closed' || thread.browser.row_count)" class="ai-browser" aria-label="探索结果">
            <header class="ai-browser-heading"><h3>探索结果</h3><span :class="{ waiting: thread.browser.status === 'waiting_user' }">{{ ({ closed: '已结束', native: '采集中', open: '浏览器已打开', waiting_user: '需要你处理' })[thread.browser.status] || '探索中' }}</span></header>
            <div v-if="thread.browser.row_count" class="ai-browser-result"><p><strong>{{ thread.browser.row_count }}</strong> 条已提取数据</p><a :href="`/api/ai/threads/${thread.id}/browser/results.csv`">下载 CSV <span aria-hidden="true">↓</span></a></div>
            <p v-else class="ai-note">暂未提取数据</p>
            <p v-if="thread.browser.status === 'waiting_user'" class="ai-browser-prompt">请在运行 SpiderFly 的电脑上完成浏览器操作，然后继续。</p>
            <details :key="thread.id + ':' + thread.browser.status" class="ai-disclosure" :open="thread.browser.status === 'waiting_user'">
              <summary>查看探索详情</summary>
              <p v-if="thread.browser.message">{{ thread.browser.message }}</p>
              <p v-if="thread.browser.status === 'open'">独立窗口位于运行 SpiderFly 的 Windows 电脑，当前会话保留登录状态。</p>
              <p v-if="thread.browser.url" class="ai-browser-url">{{ thread.browser.url }}</p>
            </details>
            <div v-if="['open', 'waiting_user', 'native'].includes(thread.browser.status)" class="ai-draft-actions">
              <button v-if="thread.browser.status === 'waiting_user'" class="button primary" :disabled="busy || Boolean(active)" @click="browserAction('resume')">我已处理，继续</button>
              <button class="button ghost" :disabled="busy" @click="browserAction('close')">{{ thread.browser.status === 'native' ? '结束本次采集' : '关闭浏览器' }}</button>
            </div>
          </section>
          <div class="ai-side-heading"><div><span>OUTPUT</span><h3>{{ showHistory ? '草稿版本' : '最新草稿' }}</h3></div><small>{{ thread?.drafts?.length || 0 }} 个版本</small></div>
          <article v-for="draft in visibleDrafts" :key="draft.id" class="ai-draft">
            <strong>v{{ draft.version }} · {{ draft.name }}</strong><div class="ai-draft-status"><span class="mini-badge neutral-badge">{{ draft.trial ? ({ pending: '等待试跑', running: '正在采集', success: '试跑通过', failed: '试跑未通过', cancelled: '已停止', interrupted: '已中断' }[draft.trial.status]) : '未试运行' }}</span></div>
            <p v-if="draft.trial?.message">{{ draft.trial.message }}</p>
            <div v-if="draft.trial?.files?.length" class="ai-links"><a v-for="file in draft.trial.files" :key="file" :href="`/api/ai/collection-trials/${draft.trial.id}/files/${encodeURIComponent(file)}`">{{ file }}</a></div>
            <details v-if="draft.trial?.log" class="ai-disclosure"><summary>试跑日志</summary><pre class="ai-trial-log">{{ draft.trial.log }}</pre></details>
            <details v-if="draft.description" class="ai-disclosure"><summary>草稿说明</summary><p>{{ draft.description }}</p></details>
            <div class="ai-draft-actions"><button class="button ghost" @click="preview(draft)">查看代码</button><button class="button primary" :disabled="Boolean(active)" @click="useDraft(draft)">{{ thread?.task_id ? '提交并验证' : '使用草稿' }}</button></div>
            <div class="ai-links"><a :href="`/api/ai/drafts/${draft.id}/download`">下载 Python</a><a :href="`/api/ai/drafts/${draft.id}/download?file=requirements.txt`">下载依赖</a></div>
          </article>
          <div v-if="!thread?.drafts?.length" class="ai-empty-draft"><span aria-hidden="true">{ }</span><strong>还没有草稿</strong><p>明确需求后，AI 生成的 Python 草稿会出现在这里。</p></div>
          <button v-if="thread?.drafts?.length > 1" class="button ghost" :aria-expanded="showHistory" @click="showHistory = !showHistory">{{ showHistory ? '收起历史' : `历史草稿（${thread.drafts.length - 1}）` }}</button>
          <details class="ai-disclosure ai-records"><summary>处理记录与用量</summary>
            <p class="ai-note">{{ setting?.model }} · {{ tokens.toLocaleString() }} tokens</p>
            <ol v-if="thread?.events?.length" class="ai-events"><li v-for="item in thread.events" :key="item.id"><span>{{ item.message }}</span><small>{{ new Date(item.created_at).toLocaleTimeString('zh-CN') }}</small></li></ol>
            <p v-else class="ai-note">暂无记录</p>
          </details>
        </aside>
      </div>
      <div v-if="selectedDraft" class="ai-code"><header><strong>v{{ selectedDraft.version }} · main.py</strong><button class="button ghost" @click="selectedDraft = null">收起代码</button></header><pre>{{ selectedDraft.source }}</pre><p>依赖：{{ selectedDraft.requirements || '仅标准库' }}</p></div>
      <AiMaintenance v-if="taskId" :key="taskId" :task-id="taskId" @open-execution="id => emit('open-execution', id)" />
    </section>
  </div>
</template>

<style scoped>
.ai-browser { margin-bottom: 24px; padding: 16px; border: 1px solid #dfe8e0; border-radius: 12px; background: #fff; font-size: 12px; }
.ai-browser-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.ai-browser-heading h3 { margin: 0; color: #28352c; font-size: 14px; }
.ai-browser-heading > span { color: #61736b; font-size: 11px; }
.ai-browser-heading > span.waiting { color: #9a5a15; }
.ai-browser-result { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; padding: 16px 0 10px; }
.ai-browser-result strong { margin-right: 4px; color: #26332b; font-size: 26px; font-weight: 650; }
.ai-browser-result a { padding: 7px 10px; border: 1px solid #bcdac6; border-radius: 7px; color: #08783a; text-decoration: none; }
.ai-browser-result a:hover { background: #edf7f0; }
.ai-browser .ai-disclosure { border-top: 1px solid #e3ebe6; }
.ai-browser .ai-draft-actions { margin-bottom: 0; }
.ai-browser .ai-browser-prompt { color: #9a5a15; line-height: 1.7; }
.ai-chat > .ai-error { flex-shrink: 0; max-height: 96px; overflow: auto; margin: 0 0 10px; }
.ai-browser p { margin: 6px 0; color: #61736b; }
.ai-browser-url { overflow-wrap: anywhere; }

.ai-layer {
  z-index: 60;
  padding: 18px;
  background: rgba(25, 35, 29, .52);
  backdrop-filter: blur(5px);
}
.ai-dialog {
  width: min(1240px, calc(100vw - 36px));
  max-height: calc(100vh - 36px);
  overflow: auto;
  border: 1px solid rgba(31, 66, 47, .16);
  border-radius: 22px;
  background: #fff;
  box-shadow: 0 32px 100px rgba(20, 45, 32, .28);
}
.ai-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  min-height: 88px;
  padding: 16px 28px;
  border-bottom: 1px solid #e3ebe6;
  background: linear-gradient(110deg, #fff 0%, #fff 62%, #f2f8f4 100%);
}
.ai-heading-copy { display: grid; gap: 3px; }
.ai-eyebrow {
  color: #138247;
  font-family: Consolas, monospace;
  font-size: 10px;
  font-weight: 750;
  letter-spacing: .13em;
}
.ai-heading h2 { margin: 0; color: #202923; font-size: 23px; line-height: 30px; }
.ai-heading p { margin: 0; color: #78847c; font-size: 12px; line-height: 18px; }
.ai-heading .modal-close {
  width: 34px;
  height: 38px;
  border: 1px solid transparent;
  border-radius: 50%;
  font-size: 19px;
}
.ai-heading .modal-close:hover { border-color: #dce7df; background: #fff; }
.ai-layout { display: grid; grid-template-columns: minmax(0, 1.72fr) minmax(310px, .88fr); }
.ai-chat { display: flex; flex-direction: column; min-width: 0; height: clamp(460px, calc(100dvh - 154px), 820px); padding: 16px 24px 20px; }
.ai-history {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  flex-shrink: 0;
  align-items: center;
  gap: 16px;
  padding: 0 0 12px;
  border-bottom: 1px solid #e0e9e3;
}
.ai-new-thread {
  display: inline-flex;
  min-width: 112px;
  height: 38px;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 0 10px;
  border: 1px solid #DFE3DF;
  border-radius: 8px;
  background: #fff;
  color: #292B28;
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
  transition: border-color 140ms ease, background 140ms ease, transform 140ms ease;
}
.ai-new-thread span {
  display: grid;
  width: 16px;
  height: 16px;
  place-items: center;
  border-radius: 7px;
  background: transparent;
  font-size: 17px;
  line-height: 1;
}
.ai-new-thread:hover:not(:disabled) { border-color: #73b98c; background: #f3faf5; transform: translateY(-1px); }
.ai-new-thread:disabled { cursor: not-allowed; opacity: .5; }
.ai-messages {
  flex: 1;
  min-height: 0;
  overscroll-behavior: contain;
  overflow: auto;
  padding: 18px 4px 8px 0;
  scrollbar-color: #aebbb2 transparent;
  scrollbar-width: thin;
}
.ai-message {
  margin: 0 0 14px;
  padding: 14px 16px 16px;
  overflow-wrap: anywhere;
  border: 1px solid #e1e9e4;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 5px 18px rgba(38, 68, 49, .045);
}
.ai-message.assistant { margin-right: 28px; border-left: 3px solid #72b98b; }
.ai-message.user { margin-left: 58px; border-color: #d5e8dc; background: #edf7f0; box-shadow: none; }
.ai-message-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 9px; }
.ai-message-meta span {
  display: grid;
  width: 25px;
  height: 25px;
  place-items: center;
  border-radius: 8px;
  background: #e5f4ea;
  color: #087a3b;
  font-family: Consolas, monospace;
  font-size: 9px;
  font-weight: 800;
}
.ai-message.user .ai-message-meta span { background: #fff; color: #4f6f5b; }
.ai-message-meta strong { color: #47705a; font-size: 11px; letter-spacing: .02em; }
.ai-message > div { white-space: pre-wrap; color: #303a33; font-size: 14px; line-height: 1.85; }
.ai-intro { display: grid; height: 100%; align-content: center; padding: 24px 10px; color: #60736a; line-height: 1.8; text-align: center; }
.ai-intro h3 { margin: 0; color: #26352b; font-size: 19px; font-weight: 650; }
.ai-intro p { margin: 8px 0; font-size: 13px; }
.ai-status { display: flex; align-items: center; gap: 8px; min-height: 34px; padding: 4px 2px 10px; color: #557163; font-size: 11px; }
.ai-status i { width: 7px; height: 7px; border-radius: 50%; background: #8a9a90; }
.ai-status i.active { background: #009139; box-shadow: 0 0 0 5px rgba(0, 145, 57, .09); animation: ai-pulse 1.5s ease-in-out infinite; }
.ai-compose {
  flex-shrink: 0;
  padding: 10px;
  border: 1px solid #d9e5dd;
  border-radius: 15px;
  background: #fbfcfb;
  box-shadow: 0 8px 24px rgba(35, 68, 47, .06);
}
.ai-compose:focus-within { border-color: #80bd95; box-shadow: 0 0 0 3px rgba(0, 145, 57, .08), 0 8px 24px rgba(35, 68, 47, .06); }
.ai-compose textarea {
  width: 100%;
  display: block;
  height: 64px;
  min-height: 64px;
  max-height: 160px;
  resize: vertical;
  padding: 4px 5px;
  border: 0;
  outline: 0;
  background: transparent;
  color: #26332b;
  font: inherit;
  line-height: 1.7;
}
.ai-compose > div { display: flex; align-items: center; gap: 9px; margin-top: 5px; }
.ai-compose small { display: flex; flex: 1; align-items: center; gap: 7px; color: #7a877e; font-size: 11px; }
.ai-compose small i { width: 6px; height: 6px; border-radius: 50%; background: #22a05a; }
.ai-send { min-width: 72px; border-radius: 9px; }
.ai-error { margin: 16px 28px 0; padding: 12px 16px; border-radius: 10px; background: #fff2ef; color: #a53e32; font-size: 13px; white-space: pre-wrap; }
.ai-side {
  min-width: 0;
  max-height: clamp(460px, calc(100dvh - 154px), 820px);
  overflow-y: auto;
  padding: 20px;
  border-left: 1px solid #e1e9e3;
  background: #f5f8f6;
}
.ai-side-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; padding-bottom: 16px; border-bottom: 1px solid #dfe7e1; }
.ai-side-heading > div { display: grid; gap: 2px; }
.ai-side-heading span { color: #218451; font-family: Consolas, monospace; font-size: 9px; font-weight: 800; letter-spacing: .12em; }
.ai-side-heading h3 { margin: 0; color: #28352c; font-size: 15px; }
.ai-side-heading small { color: #8a958d; font-size: 10px; }
.ai-empty-draft { display: grid; min-height: 180px; place-content: center; justify-items: center; gap: 8px; text-align: center; }
.ai-empty-draft > span { display: grid; width: 48px; height: 48px; place-items: center; border: 1px solid #d6e5db; border-radius: 14px; background: #fff; color: #259059; font-family: Consolas, monospace; font-size: 14px; }
.ai-empty-draft strong { color: #405147; font-size: 12px; }
.ai-empty-draft p { max-width: 220px; margin: 0; color: #8a958d; font-size: 11px; line-height: 18px; }
.ai-note { color: #687c75; font-size: 13px; line-height: 1.7; }
.ai-draft { margin: 16px 0 12px; padding: 15px; border: 1px solid #dfe8e0; border-radius: 12px; background: #fff; }
.ai-draft p { color: #61736b; font-size: 13px; line-height: 1.6; }
.ai-draft-status { margin-top: 10px; }
.ai-draft-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.ai-links { display: flex; gap: 16px; font-size: 12px; }
.ai-links a { color: #287251; }
.ai-disclosure { color: #60746a; font-size: 12px; line-height: 1.7; }
.ai-disclosure summary { padding: 7px 0; cursor: pointer; }
.ai-records { margin-top: 18px; padding-top: 8px; border-top: 1px solid #dfe7e1; }
.ai-trial-log { max-height: 220px; overflow: auto; font-size: 12px; overflow-wrap: anywhere; white-space: pre-wrap; }
.ai-events { max-height: 250px; overflow: auto; padding: 0; list-style: none; font-size: 12px; }
.ai-events li { display: flex; justify-content: space-between; gap: 8px; padding: 10px 0; border-bottom: 1px solid #e3e9e4; }
.ai-events small { color: #819087; white-space: nowrap; }
.ai-code { padding: 20px 26px; border-top: 1px solid #e1e9e3; }
.ai-code header { display: flex; align-items: center; justify-content: space-between; }
.ai-code pre { max-height: 45vh; overflow: auto; padding: 18px; border-radius: 12px; background: #162a22; color: #e1eee5; font-size: 12px; line-height: 1.6; }
.ai-code p { font-size: 12px; white-space: pre-wrap; }
@keyframes ai-pulse { 50% { opacity: .45; transform: scale(.82); } }
@media (prefers-reduced-motion: reduce) { .ai-status i.active, .ai-new-thread { animation: none; transition: none; } }
@media (max-width: 760px) {
  .ai-layer { padding: 8px; }
  .ai-dialog { width: calc(100vw - 16px); max-height: calc(100vh - 16px); border-radius: 16px; }
  .ai-heading { min-height: 92px; padding: 17px 18px; }
  .ai-heading p { display: none; }
  .ai-layout { grid-template-columns: 1fr; }
  .ai-chat { padding: 15px; }
  .ai-history { gap: 8px; }
  .ai-new-thread { min-width: 96px; }
  .ai-message.assistant { margin-right: 12px; }
  .ai-message.user { margin-left: 24px; }
  .ai-side { max-height: none; overflow: visible; border-top: 1px solid #e1e9e3; border-left: 0; }
  .ai-status, .ai-compose > div { flex-wrap: wrap; }
  .ai-compose small { flex-basis: 100%; }
}
</style>
