<script setup>
import { onMounted } from 'vue'
import { useWorkspaceContext } from '../../workspace/context'
import TaskRunSettings from '../tasks/TaskRunSettings.vue'
import DependencyFields from './DependencyFields.vue'

const props = defineProps({ embedded: Boolean, disabled: Boolean, formId: { type: String, default: 'create-task-form' } })
const emit = defineEmits(['submit'])
const { appForm, isAdmin, openAi, selectScript, loadTargetHosts, targetHosts, targetHostsLoading,
  uploadApp, uploadKey, uploading, uploadError, uploadUncertain, requirementsReading, handleSessionExpired } = useWorkspaceContext()
onMounted(loadTargetHosts)
function submit() { if (props.embedded) emit('submit'); else uploadApp() }
</script>

<template>
  <section :class="embedded ? 'create-task-embedded' : 'view-stack'">
    <section v-if="!embedded" class="panel create-ai-entry"><div><strong>用 AI 创建任务</strong></div><button class="button secondary" type="button" @click="openAi()">AI 创建</button></section>
    <section v-if="isAdmin" :class="{ panel: !embedded }">
      <form :id="formId" class="app-upload-form" @submit.prevent="submit">
        <fieldset class="create-task-fields" :disabled="disabled || uploading || uploadUncertain">
          <legend class="sr-only">单任务设置</legend>
          <div v-if="uploadError" class="creation-feedback error" role="alert">{{ uploadError }}</div>
          <div class="editor-layout">
            <section class="editor-main" aria-label="任务内容">
              <div class="editor-section-heading"><h3>任务内容</h3></div>
              <div class="upload-basics">
                <label class="field"><span>任务名称</span><input v-model="appForm.name" type="text" maxlength="100" required placeholder="例如：财务日报" /></label>
                <label class="field"><span>任务说明 <small>可选</small></span><input v-model="appForm.description" type="text" maxlength="500" placeholder="简要说明用途" /></label>
              </div>
              <label class="upload-file-card required-file script-upload" :class="{ selected: appForm.script }">
                <input :key="uploadKey" type="file" accept=".py,text/x-python" @change="selectScript" />
                <span class="upload-file-mark">PY</span>
                <span class="upload-file-copy"><strong>{{ appForm.script?.name || '上传 Python 脚本' }}</strong><small>{{ appForm.script ? '已选择 · 保存后准备依赖' : '选择 .py 文件，自动识别依赖' }}</small></span>
                <span class="upload-file-action">{{ appForm.script ? '更换文件' : '选择文件' }}</span>
              </label>
              <DependencyFields :key="uploadKey" :script="appForm.script" v-model:requirements="appForm.requirements_text" v-model="appForm.dependency_decisions" :target="appForm.target_host_id" :disabled="disabled || uploading || uploadUncertain" @state="appForm.dependency_status = $event.status" @unauthorized="handleSessionExpired" />
            </section>
            <TaskRunSettings :form="appForm" :hosts="targetHosts" :hosts-loading="targetHostsLoading" :disabled="disabled || uploading || uploadUncertain" :identity="uploadKey" creating @unauthorized="handleSessionExpired" />
          </div>
        </fieldset>
        <div v-if="!embedded" class="upload-actions"><button class="button primary" type="submit" :disabled="uploading || uploadUncertain || requirementsReading || Boolean(appForm.dependency_status && !['ready','idle'].includes(appForm.dependency_status))">{{ uploading ? '正在创建…' : '创建任务' }}</button></div>
      </form>
    </section>
  </section>
</template>

<style scoped>
.create-task-embedded, .create-task-fields { display: grid; min-width: 0; }
.create-task-fields { border: 0; padding: 0; margin: 0; width: 100%; gap: 16px; }
.create-task-embedded .app-upload-form { padding: 0; min-width: 0; }
.upload-basics { grid-template-columns: minmax(0, 1fr); gap: 16px; }
.script-upload { min-height: 116px; padding: 20px; gap: 14px; border-color: #bdd9c6; border-radius: 12px; background: #fbfdfb; }
.script-upload:hover { background: var(--brand-soft); border-color: var(--brand); }
.script-upload:focus-within { outline: 2px solid var(--brand); outline-offset: 3px; }
.script-upload .upload-file-mark { width: 42px; height: 46px; border-radius: 10px; font-size: 12px; }
.script-upload .upload-file-copy { gap: 6px; }
.script-upload .upload-file-copy strong { white-space: normal; overflow-wrap: anywhere; font-size: 14px; font-weight: 600; }
.script-upload .upload-file-copy small { white-space: normal; overflow-wrap: anywhere; color: var(--muted); font-size: 12px; }
.script-upload .upload-file-action { font-size: 12px; }
.creation-feedback.error { padding: 12px; border: 1px solid #f0c2b7; border-radius: 8px; background: var(--coral-soft); color: #ad4935; font-size: 12px; overflow-wrap: anywhere; }
@media (max-width: 420px) { .script-upload { padding: 16px; grid-template-columns: 38px minmax(0, 1fr); } .script-upload .upload-file-action { grid-column: 2; } .script-upload .upload-file-mark { width: 36px; height: 40px; } }
</style>
