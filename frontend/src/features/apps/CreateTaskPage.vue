<script setup>
import { onMounted } from 'vue'
import { useWorkspaceContext } from '../../workspace/context'
import PlanFields from '../schedule/PlanFields.vue'
import DependencyFields from './DependencyFields.vue'

const props = defineProps({ embedded: Boolean, disabled: Boolean, formId: { type: String, default: 'create-task-form' } })
const emit = defineEmits(['submit'])
const { appForm, isAdmin, openAi, selectRequirementsFile, selectScript, loadTargetHosts, targetHosts, targetHostsLoading,
  uploadApp, uploadKey, uploading, uploadError, uploadUncertain, requirementsReading, handleSessionExpired } = useWorkspaceContext()
onMounted(loadTargetHosts)
function submit() { if (props.embedded) emit('submit'); else uploadApp() }
</script>

<template>
  <section :class="embedded ? 'create-task-embedded' : 'view-stack'">
    <section v-if="!embedded" class="panel create-ai-entry">

      <div><strong>用 AI 创建任务</strong></div>
      <button class="button secondary" type="button" @click="openAi()">AI 创建</button>
    </section>
    <section v-if="isAdmin" :class="{ panel: !embedded }">
      <form :id="formId" class="app-upload-form" @submit.prevent="submit">
        <fieldset class="create-task-fields" :disabled="disabled || uploading || uploadUncertain">
          <legend class="sr-only">单任务设置</legend>
          <p class="create-task-help">上传 .py，自动识别并准备依赖；不会试跑脚本。</p>
          <div v-if="uploadError" class="creation-feedback error" role="alert">{{ uploadError }}</div>
          <div class="upload-basics">
            <label class="field"><span>任务名称</span><input v-model="appForm.name" type="text" maxlength="100" required placeholder="例如：财务日报" /></label>
            <label class="field"><span>任务说明 <small>可选</small></span><input v-model="appForm.description" type="text" maxlength="500" placeholder="说明任务用途" /></label>
          </div>
          <div class="upload-file-grid">
            <label class="upload-file-card required-file" :class="{ selected: appForm.script }">
              <input :key="uploadKey" type="file" accept=".py,text/x-python" @change="selectScript" />
              <span class="upload-file-mark">PY</span><span class="upload-file-copy"><strong>Python 脚本 <em>必选</em></strong><small>{{ appForm.script?.name || '选择一个 .py 文件' }}</small></span>
              <span class="upload-file-action">{{ appForm.script ? '更换' : '选择文件' }}</span>
            </label>
          </div>
          <DependencyFields :key="uploadKey" :script="appForm.script" v-model:requirements="appForm.requirements_text" v-model="appForm.dependency_decisions" :target="appForm.target_host_id" :disabled="disabled || uploading || uploadUncertain" @state="appForm.dependency_status = $event.status" @unauthorized="handleSessionExpired" />

          <section class="schedule-card create-task-settings">
            <div class="schedule-card-heading"><strong>运行设置</strong></div>
            <label class="field schedule-target-field"><span>运行电脑</span>
              <select v-model="appForm.target_host_id" :disabled="targetHostsLoading"><option value="">主控本机</option>
                <option v-for="host in targetHosts" :key="host.id" :value="String(host.id)" :disabled="host.approval_status !== 'approved'">{{ host.name }}{{ host.approval_status === 'approved' ? '' : '（不可用）' }}</option>
              </select><small>手动和计划均发往此电脑；离线或忙碌时排队。</small>
            </label>
            <PlanFields :form="appForm" :remote="Boolean(appForm.target_host_id)" :disabled="disabled || uploading || uploadUncertain" :identity="uploadKey" @unauthorized="handleSessionExpired" />
            <div class="create-settings-bottom">
              <div class="field create-plan-state"><span>任务状态</span><button class="switch-row" type="button" role="switch" :aria-checked="appForm.enabled" @click="appForm.enabled = !appForm.enabled"><i :class="{ active: appForm.enabled }"><b></b></i><span>{{ appForm.enabled ? '创建后启用' : '暂不启用' }}</span></button></div>
              <div class="notification-options"><div><strong>飞书通知</strong></div>
                <label><input v-model="appForm.notify_on_success" type="checkbox" />成功时通知</label>
                <label><input v-model="appForm.notify_on_failure" type="checkbox" />失败时通知</label>
                <label :class="{ disabled: !appForm.notify_on_failure }"><input v-model="appForm.failure_screenshot" :disabled="!appForm.notify_on_failure" type="checkbox" />失败附带截图</label>
              </div>
            </div>
          </section>
        </fieldset>
        <div v-if="!embedded" class="upload-actions"><button class="button primary" type="submit" :disabled="uploading || uploadUncertain || requirementsReading || Boolean(appForm.dependency_status && !['ready','idle'].includes(appForm.dependency_status))">{{ uploading ? '正在创建…' : '创建任务' }}</button></div>
      </form>
    </section>
  </section>
</template>

<style scoped>
.create-task-settings { border: 0; border-top: 1px solid var(--border-soft); border-radius: 0; padding: 16px 0 0; }
.create-task-embedded, .create-task-fields { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; min-width: 0; }
.create-task-fields { border: 0; padding: 0; margin: 0; width: 100%; }
.create-task-help { margin: 0; color: var(--muted); font-size: 12px; line-height: 20px; overflow-wrap: anywhere; }
.create-task-embedded .app-upload-form { padding: 0; min-width: 0; }
.create-task-embedded .upload-file-grid { grid-template-columns: minmax(0, 1fr); }
.create-task-embedded .upload-file-card { min-width: 0; }
.create-task-embedded .upload-file-copy { min-width: 0; }
.create-task-embedded .upload-file-copy small { overflow-wrap: anywhere; white-space: normal; }
.create-task-fields small, .create-task-fields em, .create-task-fields .upload-file-action, .create-task-fields .manual-requirements summary { font-size: 11px; }
.create-task-fields .upload-file-copy strong { white-space: normal; }
.create-task-fields .manual-requirements summary { width: auto; overflow-wrap: anywhere; }
.create-task-fields .notification-options { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); min-width: 0; }
.create-task-fields .notification-options > div { grid-column: 1 / -1; }
.create-task-fields .notification-options label { white-space: normal; font-size: 13px; }
.create-task-fields .create-settings-bottom { grid-template-columns: minmax(0, .6fr) minmax(0, 1.4fr); }
.creation-feedback.error { padding: 12px; border: 1px solid #f0c2b7; border-radius: 8px; background: var(--coral-soft); color: #ad4935; font-size: 12px; overflow-wrap: anywhere; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
@media (max-width: 720px) { .create-task-embedded .upload-basics, .create-task-embedded .upload-file-grid, .create-task-embedded .create-settings-bottom { grid-template-columns: minmax(0, 1fr); } }
</style>
