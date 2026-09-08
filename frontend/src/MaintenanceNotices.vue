<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { maintenanceSummary, maintenanceResult } from './maintenanceSummary'
defineProps({ canManage: Boolean })
const emit = defineEmits(['open-task', 'open-execution', 'notify'])
const state = ref({ items: [], unread_count: 0 }), open = ref(false), error = ref('')
const announced = new Set()
let timer, alive = true, loading = false
const labels = { activated: '修复已启用', review: '维护完成 · 保留原版本', failed: '维护未成功', budget: '维护预算不足', cancelled: '维护已停止', interrupted: '维护被中断' }
async function load() {
  if (loading) return
  loading = true
  try {
    const response = await fetch('/api/maintenance/notices', { credentials: 'include' })
    if (!response.ok) throw new Error('维护结果暂时无法载入')
    const value = await response.json()
    if (!alive) return
    state.value = value; error.value = ''
    const unseen = value.items.filter(item => !item.read_at && !announced.has(item.id))
    unseen.forEach(item => announced.add(item.id))
    if (unseen.length) emit('notify', `${unseen.length} 条维护结果`, '')
  } catch (cause) { if (alive) error.value = cause.message }
  finally { loading = false }
}
async function read(item) {
  try {
    const response = await fetch(`/api/maintenance/notices/${item.id}/read`, { method: 'POST', credentials: 'include' })
    if (!response.ok) throw new Error('未能标记已读，请重试')
    await load()
  } catch (cause) { error.value = cause.message }
}
onMounted(() => { load(); timer = window.setInterval(load, 6000) })
onBeforeUnmount(() => { alive = false; window.clearInterval(timer) })
</script>
<template>
  <button class="button ghost" type="button" @click="open = true; load()">维护结果<span v-if="state.unread_count" class="notice-count">{{ state.unread_count }}</span></button>
  <div v-if="open" class="modal-layer notices-layer" @mousedown.self="open = false">
    <section class="notices-dialog" role="dialog" aria-modal="true" aria-labelledby="maintenance-notices-title">
      <header><h2 id="maintenance-notices-title">维护结果</h2><button class="modal-close" aria-label="关闭维护结果" @click="open = false">×</button></header>
      <p v-if="error" role="alert">{{ error }}</p><p v-if="!state.items.length">暂无维护结果。</p>
      <article v-for="item in state.items" :key="item.id" :class="{ unread: !item.read_at }">
        <strong>{{ item.task_name }}</strong><span class="notice-status">{{ maintenanceResult(item) || labels[item.status] || item.status }}</span>
        <p>{{ maintenanceSummary(item) }}</p><small>{{ new Date(item.ended_at).toLocaleString('zh-CN') }}</small>
        <div><button v-if="item.rerun_execution_id" class="button ghost" @click="open = false; emit('open-execution', item.rerun_execution_id)">查看重跑日志</button><button v-if="canManage" class="button ghost" @click="open = false; emit('open-task', item.task_id)">查看维护记录</button><button v-if="!item.read_at" class="button secondary" @click="read(item)">我知道了</button><span v-else>已读</span></div>
      </article>
    </section>
  </div>
</template>
<style scoped>
.notice-count{background:#286e4d;color:#fff;border-radius:14px;padding:2px 7px;margin-left:6px}.notices-layer{z-index:65;padding:20px}.notices-dialog{width:min(730px,95vw);max-height:88vh;overflow:auto;background:#fff;border-radius:18px;padding:25px;box-shadow:0 25px 90px #173b3433}.notices-dialog header{display:flex;justify-content:space-between;gap:18px}.notices-dialog h2{margin:0}.notices-dialog header p{color:#687c75;font-size:13px;line-height:1.7}.notices-dialog article{border:1px solid #e1e9e4;border-radius:12px;margin-top:14px;padding:18px}.notices-dialog article.unread{border-left:4px solid #39835b;background:#f5faf6}.notices-dialog article p{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;line-height:1.8}.notice-status{display:block;color:#4c7960;font-size:12px;margin-top:8px}.notices-dialog article small{color:#7c8b82;font-size:12px}.notices-dialog article>div{display:flex;justify-content:flex-end;align-items:center;gap:10px;margin-top:12px}
</style>
