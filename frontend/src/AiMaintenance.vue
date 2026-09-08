<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import MaintenanceLog from './MaintenanceLog.vue'
import { maintenanceResult } from './maintenanceSummary'
const props = defineProps({ taskId: { type: Number, required: true } })
const emit = defineEmits(['open-execution'])
const state = ref(null), error = ref(''), code = ref(null), busy = ref(false)
const labels = { pending: '等待维护', generating: '正在分析并修复', ready: '等待空闲时试跑', testing: '正在验证修复', activated: '修复已启用', review: '维护完成 · 保留原版本', failed: '维护未成功', budget: '预算不足', cancelled: '已停止', interrupted: '已中断' }
let timer, alive = true
async function api(path, method = 'GET') {
  const response = await fetch('/api/maintenance' + path, { method, credentials: 'include' })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '维护请求失败')
  return data
}
async function load() { try { const value = await api('/tasks/' + props.taskId); if (alive) state.value = value } catch (cause) { if (alive) error.value = cause.message } }
async function action(path) {
  busy.value = true; error.value = ''
  try { await api(path, 'POST'); await load() } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
async function preview(id) { try { code.value = await api('/versions/' + id) } catch (cause) { error.value = cause.message } }
onMounted(() => { load(); timer = window.setInterval(load, 2200) })
onBeforeUnmount(() => { alive = false; window.clearInterval(timer) })
</script>
<template>
  <section class="maintenance-panel">
    <p v-if="error" role="alert">{{ error }}</p>
    <details class="maintenance-details">
      <summary>维护与版本 <small>{{ state?.jobs?.length ? '最近：' + (maintenanceResult(state.jobs[0]) || labels[state.jobs[0].status]) : '暂无维护记录' }}</small></summary>
    <template v-if="state">
      <details v-for="job in state.jobs" :key="job.id" class="maintenance-job" :open="job.id === state.jobs[0]?.id">
        <summary>{{ maintenanceResult(job) || labels[job.status] || job.status }} · 执行 #{{ job.execution_id }} <small>{{ new Date(job.created_at).toLocaleString('zh-CN') }}</small></summary>
        <MaintenanceLog :job="job" @open-execution="id => emit('open-execution', id)" />
        <div class="maintenance-actions"><button v-if="job.candidate_id" class="button ghost" @click="preview(job.candidate_id)">查看修复代码</button><button v-if="['pending','generating','ready','testing'].includes(job.status)" class="button ghost" :disabled="busy" @click="action(`/jobs/${job.id}/stop`)">停止本次维护</button></div>
      </details>
      <details class="maintenance-version-list"><summary>代码版本</summary>
        <p>{{ state.policy.runtime === 'readonly-v1' ? '受限环境 · 最长 120 秒' : '原任务环境' }}</p>
        <div v-for="version in state.versions" :key="version.id" class="maintenance-version"><span>V{{ version.sequence }} · {{ version.kind === 'original' ? '原始版本' : version.approved ? '已验证修复' : '修复候选' }} {{ version.id === state.policy.active_version_id ? '（使用中）' : '' }}</span><button class="button ghost" @click="preview(version.id)">查看</button><button v-if="version.approved && version.id !== state.policy.active_version_id" class="button ghost" :disabled="busy" @click="action(`/tasks/${taskId}/rollback/${version.id}`)">回退至 V{{ version.sequence }}</button></div>
      </details>
    </template>
    <div v-if="code"><strong>V{{ code.sequence }}</strong><button class="button ghost" @click="code = null">收起代码</button><pre>{{ code.source }}</pre></div>
    </details>
  </section>
</template>
<style scoped>
.maintenance-version-list{margin-top:14px;font-size:13px;color:#607469}.maintenance-version-list>summary{cursor:pointer;width:fit-content}
.maintenance-details>summary{cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:14px;color:#385f49;list-style:none}.maintenance-details>summary::before{content:'▸';font-size:11px}.maintenance-details[open]>summary::before{content:'▾'}.maintenance-details>summary small{margin-left:auto}
.maintenance-panel{padding:22px 28px;border-top:1px solid #e0e9e3;background:#fafcf9}.maintenance-panel h3{margin:0 0 10px;font-size:16px}.maintenance-panel p{font-size:13px;line-height:1.8;color:#607469;white-space:pre-wrap;overflow-wrap:anywhere}.maintenance-panel small{font-size:12px;color:#74877a}.maintenance-job{background:#fff;border:1px solid #dfe7e1;border-radius:9px;padding:13px;margin:12px 0;font-size:13px}.maintenance-job summary{cursor:pointer;color:#39664a}.maintenance-job summary small{margin-left:12px}.maintenance-job ol{line-height:1.9;color:#607469}.maintenance-actions,.maintenance-version{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.maintenance-version{padding:8px 0;font-size:13px}.maintenance-version>span{flex:1}.maintenance-panel pre{max-height:340px;overflow:auto;background:#172b22;color:#def0e2;padding:16px;border-radius:9px;font-size:12px;line-height:1.7}
</style>
