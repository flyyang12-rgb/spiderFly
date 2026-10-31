<script setup>
import { computed } from 'vue'
import PlanFields from '../schedule/PlanFields.vue'

const props = defineProps({
  form: { type: Object, required: true }, hosts: { type: Array, default: () => [] },
  hostsLoading: Boolean, disabled: Boolean, showTarget: { type: Boolean, default: true },
  creating: Boolean, identity: { type: [String, Number], default: '' },
})
const emit = defineEmits(['unauthorized'])
const notificationSummary = computed(() => [props.form.notify_on_success && '成功', props.form.notify_on_failure && '失败'].filter(Boolean).join('、') || '未开启')
</script>

<template>
  <section class="editor-aside task-run-settings" aria-label="运行设置">
    <div class="editor-section-heading"><h3>运行设置</h3></div>
    <label v-if="showTarget" class="field schedule-target-field"><span>运行电脑</span>
      <select v-model="form.target_host_id" :disabled="disabled || hostsLoading">
        <option value="">主控本机</option>
        <option v-for="host in hosts" :key="host.id" :value="String(host.id)" :disabled="host.approval_status !== 'approved'">{{ host.name }}{{ host.approval_status === 'approved' ? '' : '（不可用）' }}</option>
      </select>
      <small v-if="form.target_host_id">离线或忙碌时排队。</small>
    </label>
    <PlanFields :form="form" :remote="Boolean(form.target_host_id)" :disabled="disabled" :identity="identity" @unauthorized="emit('unauthorized')" />
    <div class="editor-enable-row">
      <span>{{ creating ? '创建后启用' : '启用任务' }}</span>
      <button class="switch-row" type="button" role="switch" :aria-label="creating ? '创建后启用' : '启用任务'" :aria-checked="form.enabled" :disabled="disabled" @click="form.enabled = !form.enabled"><i :class="{ active: form.enabled }"><b></b></i><span>{{ form.enabled ? '已启用' : '已停用' }}</span></button>
    </div>
    <details class="editor-disclosure notification-disclosure">
      <summary><span>飞书通知</span><small>{{ notificationSummary }}</small></summary>
      <div class="notification-options">
        <label><input v-model="form.notify_on_success" type="checkbox" :disabled="disabled" />成功时通知</label>
        <label><input v-model="form.notify_on_failure" type="checkbox" :disabled="disabled" />失败时通知</label>
        <label :class="{ disabled: !form.notify_on_failure }"><input v-model="form.failure_screenshot" type="checkbox" :disabled="disabled || !form.notify_on_failure" />失败附带截图</label>
      </div>
    </details>
    <p v-if="!creating" class="editor-footnote">最长运行 10 分钟，排队不计时。</p>
  </section>
</template>

<style scoped>
.task-run-settings { gap: 10px; padding: 16px 18px; }
.editor-enable-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-top: 1px solid var(--border); padding-top: 10px; font-size: 13px; }
.editor-enable-row .switch-row { height: 28px; font-size: 12px; }
.notification-disclosure { border-top: 1px solid var(--border); padding-top: 10px; }
.notification-options { display: flex; flex-wrap: wrap; gap: 12px 16px; border: 0; background: transparent; padding: 12px 0 0; }
.notification-options label { font-size: 12px; white-space: normal; }
.notification-options input { width: 15px; height: 15px; margin: 0; }
</style>
