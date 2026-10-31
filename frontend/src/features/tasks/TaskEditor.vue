<script setup>
import { useWorkspaceContext } from '../../workspace/context'
import TaskVersions from '../../TaskVersions.vue'
import DependencyDetails from '../apps/DependencyDetails.vue'
import TaskRunSettings from './TaskRunSettings.vue'
import WorkflowDialog from '../workflows/WorkflowDialog.vue'

const {
  editingTask,
  isAdmin,
  saveTask,
  saving,
  taskForm,
  taskModalOpen,
  targetHosts,
  targetHostsLoading,
  handleSessionExpired,
} = useWorkspaceContext()
</script>

<template>
  <WorkflowDialog title="编辑任务" wide :close-disabled="saving" @close="taskModalOpen = false">
    <fieldset class="task-edit-fields" :disabled="saving">
      <legend class="sr-only">任务设置</legend>
      <div class="editor-layout">
        <section class="editor-main" aria-label="任务内容">
          <div class="editor-section-heading"><h3>任务内容</h3></div>
          <label class="field"><span>任务名称</span><input v-model="taskForm.name" type="text" maxlength="100" placeholder="例如：订单同步" /></label>
          <label class="field"><span>任务说明 <small>可选</small></span><input v-model="taskForm.description" type="text" maxlength="500" placeholder="简要说明用途" /></label>
          <details v-if="editingTask" class="editor-disclosure edit-code-versions">
            <summary><span>脚本与版本</span><small>查看代码、依赖和历史版本</small></summary>
            <div class="editor-disclosure-body"><TaskVersions :key="editingTask.id" :task-id="editingTask.id" /><DependencyDetails :app-id="editingTask.app_id" :remote="Boolean(editingTask.target_host_id)" /></div>
          </details>
        </section>
        <TaskRunSettings :form="taskForm" :hosts="targetHosts" :hosts-loading="targetHostsLoading" :show-target="isAdmin" :disabled="saving" :identity="editingTask?.id" @unauthorized="handleSessionExpired" />
      </div>
    </fieldset>
    <template #footer><button class="button secondary" type="button" :disabled="saving" @click="taskModalOpen = false">取消</button><button class="button primary" type="button" :disabled="saving" @click="saveTask">{{ saving ? '正在保存…' : '保存' }}</button></template>
  </WorkflowDialog>
</template>
<style scoped>
.task-edit-fields { border: 0; padding: 0; margin: 0; min-width: 0; }
.edit-code-versions { min-width: 0; padding-top: 18px; border-top: 1px solid var(--border-soft); }
</style>
