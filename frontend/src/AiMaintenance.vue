<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import MaintenanceLog from './MaintenanceLog.vue'
import { maintenanceResult } from './maintenanceSummary'
const props = defineProps({ taskId: { type: Number, required: true } })
const emit = defineEmits(['open-execution'])
const state = ref(null), error = ref(''), code = ref(null), busy = ref(false)
const versionState = ref(null)
const labels = { pending: '等待维护', generating: '正在分析并修复', ready: '等待空闲时试跑', testing: '正在验证修复', activated: '修复已启用', review: '维护完成 · 保留原版本', failed: '维护未成功', budget: '预算不足', cancelled: '已停止', interrupted: '已中断' }
const versionUpdate = computed(() => versionState.value?.updates?.find(item => ['pending', 'testing', 'repairing', 'conflict', 'ready'].includes(item.status)))
let timer, alive = true
async function api(path, method = 'GET') {
  const response = await fetch('/api/maintenance' + path, { method, credentials: 'include' })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '维护请求失败')
  return data
}
async function load() {
  try {
    const [maintenance, versions] = await Promise.all([api('/tasks/' + props.taskId), versionApi('/tasks/' + props.taskId)])
    if (alive) { state.value = maintenance; versionState.value = versions }
  } catch (cause) { if (alive) error.value = cause.message }
}
async function versionApi(path, method = 'GET') {
  const response = await fetch('/api/task-versions' + path, { method, credentials: 'include' })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '版本请求失败')
  return data
}
async function action(path) {
  busy.value = true; error.value = ''
  try { await api(path, 'POST'); await load() } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
async function preview(id) { try { code.value = await api('/versions/' + id) } catch (cause) { error.value = cause.message } }
async function versionAction(path) {
  busy.value = true; error.value = ''
  try { await versionApi(path, 'POST'); await load() } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
onMounted(() => { load(); timer = window.setInterval(load, 2200) })
onBeforeUnmount(() => { alive = false; window.clearInterval(timer) })
</script>
<template>
  <section class="maintenance-panel">
    <p v-if="error" role="alert">{{ error }}</p>
    <details class="maintenance-details">
      <summary>维护与版本 <small>{{ state?.jobs?.length ? '最近：' + (maintenanceResult(state.jobs[0]) || labels[state.jobs[0].status]) : '暂无维护记录' }}</small></summary>
    <template v-if="state">
      <section v-if="versionUpdate" class="ai-version-review" :class="`is-${versionUpdate.status}`">
        <div>
          <span>{{ versionUpdate.status === 'ready' ? '候选版本已验证' : versionUpdate.status === 'conflict' ? '候选版本需要确认' : '候选版本处理中' }}</span>
          <strong>V{{ versionUpdate.sequence }}</strong>
          <p>{{ versionUpdate.note }}</p>
        </div>
        <div class="maintenance-actions">
          <template v-if="versionUpdate.status === 'ready'"><button type="button" class="button ghost" :disabled="busy" @click="versionAction(`/updates/${versionUpdate.id}/stop`)">暂不使用</button><button type="button" class="button primary" :disabled="busy" @click="versionAction(`/updates/${versionUpdate.id}/activate`)">确认使用 V{{ versionUpdate.sequence }}</button></template>
          <template v-else-if="versionUpdate.status === 'conflict'"><button type="button" class="button primary" :disabled="busy" @click="versionAction(`/updates/${versionUpdate.id}/resolve`)">按新代码继续验证</button><button type="button" class="button ghost" :disabled="busy" @click="versionAction(`/updates/${versionUpdate.id}/stop`)">保留当前版本</button></template>
          <button v-else type="button" class="button ghost" :disabled="busy" @click="versionAction(`/updates/${versionUpdate.id}/stop`)">停止更新</button>
        </div>
      </section>
      <details v-for="job in state.jobs" :key="job.id" class="maintenance-job" :open="job.id === state.jobs[0]?.id">
        <summary>{{ maintenanceResult(job) || labels[job.status] || job.status }} · 执行 #{{ job.execution_id }} <small>{{ new Date(job.created_at).toLocaleString('zh-CN') }}</small></summary>
        <MaintenanceLog :job="job" @open-execution="id => emit('open-execution', id)" />
        <div class="maintenance-actions"><button v-if="job.candidate_id" class="button ghost" @click="preview(job.candidate_id)">查看修复代码</button><button v-if="['pending','generating','ready','testing'].includes(job.status)" class="button ghost" :disabled="busy" @click="action(`/jobs/${job.id}/stop`)">停止本次维护</button></div>
      </details>
    </template>
    <section v-if="code" class="maintenance-code-preview">
      <header><div><span>修复代码</span><strong>V{{ code.sequence }}</strong></div><button type="button" class="button ghost" @click="code = null">收起代码</button></header>
      <pre>{{ code.source }}</pre>
    </section>
    </details>
  </section>
</template>
<style scoped>
.maintenance-version-list{margin-top:14px;font-size:13px;color:#607469}.maintenance-version-list>summary{cursor:pointer;width:fit-content}
.maintenance-details>summary{cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:14px;color:#385f49;list-style:none}.maintenance-details>summary::before{content:'▸';font-size:11px}.maintenance-details[open]>summary::before{content:'▾'}.maintenance-details>summary small{margin-left:auto}
.maintenance-panel{padding:22px 28px;border-top:1px solid #e0e9e3;background:#fafcf9}.maintenance-panel h3{margin:0 0 10px;font-size:16px}.maintenance-panel p{font-size:13px;line-height:1.8;color:#607469;white-space:pre-wrap;overflow-wrap:anywhere}.maintenance-panel small{font-size:12px;color:#74877a}.maintenance-job{background:#fff;border:1px solid #dfe7e1;border-radius:9px;padding:13px;margin:12px 0;font-size:13px}.maintenance-job summary{cursor:pointer;color:#39664a}.maintenance-job summary small{margin-left:12px}.maintenance-job ol{line-height:1.9;color:#607469}.maintenance-actions,.maintenance-version{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.maintenance-version{padding:8px 0;font-size:13px}.maintenance-version>span{flex:1}.maintenance-panel pre{max-height:340px;overflow:auto;background:#172b22;color:#def0e2;padding:16px;border-radius:9px;font-size:12px;line-height:1.7}
.maintenance-code-preview{margin-top:15px;border:1px solid #dbe6de;border-radius:13px;background:#fff;overflow:hidden}.maintenance-code-preview header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px 14px;border-bottom:1px solid #e5ece7;background:#f8fbf9}.maintenance-code-preview header>div{display:flex;align-items:center;gap:9px}.maintenance-code-preview header span{color:#7b8d81;font-size:12px}.maintenance-code-preview header strong{color:#244b34;font-size:14px}.maintenance-code-preview pre{margin:0;border-radius:0}
.ai-version-review{display:flex;align-items:center;justify-content:space-between;gap:18px;margin:14px 0;padding:14px 16px;border:1px solid #dce7df;border-radius:12px;background:#fff}.ai-version-review.is-ready{border-color:#9fd2b1;background:#f3fbf6}.ai-version-review.is-conflict{border-color:#ead4b8;background:#fffaf3}.ai-version-review>div:first-child{min-width:0}.ai-version-review span{display:block;margin-bottom:3px;color:#708176;font-size:11px}.ai-version-review strong{color:#224b33;font-size:17px}.ai-version-review p{margin:5px 0 0!important;line-height:1.5!important}.ai-version-review .maintenance-actions{flex:none}
</style>
