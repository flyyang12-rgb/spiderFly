<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { maintenanceSummary, maintenanceResult } from './maintenanceSummary'
const props = defineProps({ canManage: Boolean })
const emit = defineEmits(['open-task', 'open-execution', 'open-remote-run', 'notify'])
const state = ref({ items: [], unread_count: 0 }), open = ref(false), error = ref('')
const noticeTrigger = ref(null), noticeDialog = ref(null)
let previousFocus = null
const announced = new Set()
let timer, alive = true, loading = false
const labels = { activated: '修复已启用', review: '维护完成 · 保留原版本', failed: '维护未成功', budget: '维护预算不足', cancelled: '维护已停止', interrupted: '维护被中断' }
async function load() {
  if (loading) return
  loading = true
  try {
    const requests = [fetch('/api/maintenance/notices', { credentials: 'include' })]
    if (props.canManage) requests.push(fetch('/api/remote-run-notices', { credentials: 'include' }))
    const responses = await Promise.all(requests)
    if (responses.some((response) => !response.ok)) throw new Error('运行提醒暂时无法载入')
    const values = await Promise.all(responses.map((response) => response.json()))
    const maintenance = values[0]
    const remote = values[1] || { items: [], unread_count: 0 }
    const value = {
      unread_count: maintenance.unread_count + remote.unread_count,
      items: [
        ...maintenance.items.map((item) => ({ ...item, notice_type: 'maintenance', notice_key: `maintenance:${item.id}`, occurred_at: item.ended_at })),
        ...remote.items.map((item) => ({ ...item, notice_type: 'remote', notice_key: `remote:${item.id}`, occurred_at: item.finished_at })),
      ].sort((left, right) => String(right.occurred_at).localeCompare(String(left.occurred_at))).slice(0, 50),
    }
    if (!alive) return
    state.value = value; error.value = ''
    const unseen = value.items.filter(item => !item.read_at && !announced.has(item.notice_key))
    unseen.forEach(item => announced.add(item.notice_key))
    if (unseen.length) emit('notify', `${unseen.length} 条运行提醒`, '')
  } catch (cause) { if (alive) error.value = cause.message }
  finally { loading = false }
}
async function read(item) {
  try {
    const url = item.notice_type === 'remote'
      ? `/api/remote-run-notices/${item.id}/read`
      : `/api/maintenance/notices/${item.id}/read`
    const response = await fetch(url, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: '{}' })
    if (!response.ok) throw new Error('未能标记已读，请重试')
    await load()
  } catch (cause) { error.value = cause.message }
}
function noticeKeydown(event) {
  if (event.key === 'Escape') { event.preventDefault(); open.value = false; return }
  if (event.key !== 'Tab') return
  const targets = [...noticeDialog.value.querySelectorAll('button:not(:disabled), a[href], [tabindex="0"]')].filter((target) => target.getClientRects().length)
  if (!targets.length) { event.preventDefault(); noticeDialog.value.focus(); return }
  const first = targets[0], last = targets[targets.length - 1]
  if (event.shiftKey && (document.activeElement === first || document.activeElement === noticeDialog.value)) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && (document.activeElement === last || document.activeElement === noticeDialog.value)) { event.preventDefault(); first.focus() }
}
watch(open, async (shown) => {
  if (shown) {
    previousFocus = document.activeElement
    await nextTick()
    if (open.value) noticeDialog.value?.focus()
  } else if (previousFocus?.isConnected) previousFocus.focus()
})
onMounted(() => { load(); timer = window.setInterval(load, 6000) })
onBeforeUnmount(() => { alive = false; window.clearInterval(timer) })
</script>
<template>
  <button ref="noticeTrigger" class="button ghost" type="button" @click="open = true; load()">运行提醒<span v-if="state.unread_count" class="notice-count">{{ state.unread_count }}</span></button>
  <div v-if="open" class="modal-layer notices-layer" @mousedown.self="open = false">
    <section ref="noticeDialog" class="notices-dialog" role="dialog" aria-modal="true" aria-labelledby="maintenance-notices-title" tabindex="-1" @keydown="noticeKeydown">
      <header class="notices-header"><h2 id="maintenance-notices-title">运行提醒</h2><button type="button" class="modal-close" aria-label="关闭运行提醒" @click="open = false">×</button></header>
      <div class="notices-body">
        <p v-if="error" class="notices-error" role="alert">{{ error }}</p><p v-if="!error && !state.items.length" class="notices-empty">暂无运行提醒</p>
        <article v-for="item in state.items" :key="item.notice_key" :class="{ unread: !item.read_at }">
          <div class="notice-item-heading"><strong>{{ item.task_name }}</strong><span v-if="!item.read_at" class="notice-unread">未读</span><small>{{ new Date(item.occurred_at).toLocaleString('zh-CN') }}</small></div>
          <span class="notice-status">{{ item.notice_type === 'remote' ? `远程运行${item.status === 'timed_out' ? '超时' : '失败'} · ${item.host_name}` : (maintenanceResult(item) || labels[item.status] || item.status) }}</span>
          <p>{{ item.notice_type === 'remote' ? (item.error || '未返回错误摘要，请查看日志。') : maintenanceSummary(item) }}</p>
          <div class="notice-item-actions"><button v-if="item.notice_type === 'remote'" type="button" class="button ghost compact" @click="open = false; emit('open-remote-run', item.id)">查看远程运行</button><button v-if="item.rerun_execution_id" type="button" class="button ghost compact" @click="open = false; emit('open-execution', item.rerun_execution_id)">查看重跑日志</button><button v-if="item.notice_type === 'maintenance' && canManage" type="button" class="button ghost compact" @click="open = false; emit('open-task', item.task_id)">查看维护记录</button><button v-if="!item.read_at" type="button" class="button secondary compact" @click="read(item)">标为已读</button><span v-else>已读</span></div>
        </article>
      </div>
    </section>
  </div>
</template>
<style scoped>
.notice-count { margin-left: 6px; padding: 2px 6px; border-radius: 6px; background: #eaf6ed; color: #00752e; font-size: 11px; }
.notices-layer { z-index: 65; padding: 20px; }
.notices-dialog { display: flex; flex-direction: column; width: min(730px, 100%); max-height: min(780px, calc(100dvh - 40px)); overflow: hidden; border: 1px solid #dfe3df; border-radius: 12px; background: #fff; box-shadow: 0 16px 48px #2218141a; outline: none; }
.notices-header { display: flex; align-items: center; justify-content: space-between; flex: 0 0 auto; gap: 16px; padding: 20px 24px; border-bottom: 1px solid #ecefec; }
.notices-header h2 { margin: 0; color: #221814; font-size: 18px; line-height: 26px; }
.notices-body { min-height: 0; padding: 0 24px; overflow: auto; overscroll-behavior: contain; }
.notices-dialog article { padding: 20px 0; }
.notices-dialog article + article { border-top: 1px solid #ecefec; }
.notice-item-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.notice-item-heading strong { min-width: 0; color: #221814; font-size: 14px; overflow-wrap: anywhere; }
.notice-item-heading small { margin-left: auto; color: #757a75; font-size: 11px; }
.notice-unread { color: #00752e; font-size: 11px; }
.notice-status { display: block; margin-top: 8px; color: #656d65; font-size: 12px; line-height: 20px; overflow-wrap: anywhere; }
.notices-dialog article p { margin: 8px 0 0; color: #292b28; font-size: 13px; line-height: 1.75; white-space: pre-wrap; overflow-wrap: anywhere; }
.notice-item-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.notice-item-actions > span { color: #757a75; font-size: 11px; }
.notices-error { margin: 16px 0; padding: 12px; border: 1px solid #efd0c6; border-radius: 8px; background: #fff4f0; color: #a53721; font-size: 13px; overflow-wrap: anywhere; }
.notices-empty { padding: 40px 0; color: #757a75; font-size: 13px; text-align: center; }
.notices-dialog :is(button, a):focus-visible { outline: 3px solid #00913940; outline-offset: 2px; }
@media (max-width: 480px) { .notices-layer { padding: 10px; } .notices-dialog { max-height: calc(100dvh - 20px); } .notices-header { padding: 16px; } .notices-body { padding: 0 16px; } .notice-item-heading small { flex-basis: 100%; margin: 0; } }
</style>
