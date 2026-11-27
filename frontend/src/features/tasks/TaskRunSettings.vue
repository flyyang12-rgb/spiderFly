<script setup>
import UiSelect from '../../components/UiSelect.vue'
import { computed } from 'vue'
import PlanFields from '../schedule/PlanFields.vue'

const props = defineProps({
  form: { type: Object, required: true }, hosts: { type: Array, default: () => [] },
  hostsLoading: Boolean, disabled: Boolean, showTarget: { type: Boolean, default: true },
  showNotifications: { type: Boolean, default: true },
  project: Boolean, creating: Boolean, identity: { type: [String, Number], default: '' },
})
const emit = defineEmits(['unauthorized'])
const notificationSummary = computed(() => [props.form.notify_on_success && '成功', props.form.notify_on_failure && '失败'].filter(Boolean).join('、') || '未开启')
</script>

<template>
  <section :class="creating ? 'create-settings' : 'editor-aside task-run-settings'" aria-label="运行设置">
    <div v-if="!creating" class="editor-section-heading"><h3>运行设置</h3></div>
    <div :class="{ 'program-target': creating }">
      <h3 v-if="creating">执行程序与电脑</h3>
      <slot name="program" />
      <label v-if="showTarget" class="field schedule-target-field"><span>运行电脑</span>
        <UiSelect v-model="form.target_host_id" label="运行电脑" :disabled="disabled || hostsLoading" :options="[{ value: '', label: '主控部署机' }, ...hosts.map(host => ({ value: String(host.id), label: host.name + (host.approval_status === 'approved' ? '' : '（不可用）'), disabled: host.approval_status !== 'approved' }))]" />
        <small v-if="project && hosts.some(host => String(host.id) === String(form.target_host_id) && !host.supports_project_zip)">ZIP 任务需 Agent 0.5.3 或以上，当前可保存计划。</small>
        <small v-else-if="form.target_host_id">离线或忙碌时排队。</small>
        <small v-else>部署 SpiderFly 的电脑。</small>
      </label>
    </div>
    <PlanFields :form="form" :remote="Boolean(form.target_host_id)" :disabled="disabled" :identity="identity" @unauthorized="emit('unauthorized')" />
    <component :is="creating ? 'details' : 'div'" class="run-options">
      <summary v-if="creating">高级设置</summary>
      <slot name="advanced" />
      <div class="editor-enable-row">
        <span>{{ creating ? '创建后启用' : '启用任务' }}</span>
        <button class="switch-row" type="button" role="switch" :aria-label="creating ? '创建后启用' : '启用任务'" :aria-checked="form.enabled" :disabled="disabled" @click="form.enabled = !form.enabled"><i :class="{ active: form.enabled }"><b></b></i><span>{{ form.enabled ? '已启用' : '已停用' }}</span></button>
      </div>
      <details v-if="showNotifications" class="editor-disclosure notification-disclosure">
        <summary><span>飞书通知</span><small>{{ notificationSummary }}</small></summary>
        <div class="notification-options">
          <label><input v-model="form.notify_on_success" type="checkbox" :disabled="disabled" />成功时通知</label>
          <label><input v-model="form.notify_on_failure" type="checkbox" :disabled="disabled" />失败时通知</label>
          <label :class="{ disabled: !form.notify_on_failure }"><input v-model="form.failure_screenshot" type="checkbox" :disabled="disabled || !form.notify_on_failure" />失败附带截图</label>
        </div>
      </details>
    </component>
    <p v-if="!creating" class="editor-footnote">最长运行 10 分钟，排队不计时。</p>
  </section>
</template>

<style scoped>
.task-run-settings { gap: 10px; padding: 16px 18px; }
.editor-enable-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-top: 1px solid var(--border); padding-top: 10px; font-size: var(--workspace-body); }
.editor-enable-row .switch-row { height: 28px; font-size: var(--workspace-body); }
.notification-disclosure { border-top: 1px solid var(--border); padding-top: 10px; }
.notification-options { display: flex; flex-wrap: wrap; gap: 12px 16px; border: 0; background: transparent; padding: 12px 0 0; }
.notification-options label { font-size: var(--workspace-body); white-space: normal; }
.notification-options input { width: 15px; height: 15px; margin: 0; }
.create-settings, .program-target, .run-options { display: grid; gap: 12px; min-width: 0; }
.create-settings { gap: 20px; }
.program-target { padding: 14px 16px; border: 1px solid var(--border); border-radius: 10px; }
.program-target h3 { margin: 0; font-size: var(--ui-heading); }
details.run-options { display: block; border-top: 1px solid var(--border-soft); padding-top: 12px; }
details.run-options > summary { cursor: pointer; font-size: var(--workspace-body); font-weight: 600; }
details.run-options[open] > :not(summary) { margin-top: 12px; }
</style>
