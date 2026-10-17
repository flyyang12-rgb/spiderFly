<script setup>
import { useWorkspaceContext } from '../../workspace/context'
import TaskVersions from '../../TaskVersions.vue'
import DependencyDetails from '../apps/DependencyDetails.vue'
import PlanFields from '../schedule/PlanFields.vue'
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
  <WorkflowDialog title="编辑任务" :close-disabled="saving" @close="taskModalOpen = false">
      <fieldset class="task-edit-fields" :disabled="saving">
        <label class="field"
          ><span>任务名称</span
          ><input v-model="taskForm.name" type="text" maxlength="100" placeholder="例如：订单同步"
        /></label>
        <label class="field"
          ><span>任务说明 <small>可选</small></span
          ><input v-model="taskForm.description" type="text" maxlength="500" placeholder="说明任务用途"
        /></label>

        <section class="schedule-card">
          <div class="schedule-card-heading">
            <div><strong>运行设置</strong></div>
          </div>
          <label v-if="isAdmin" class="field schedule-target-field">
            <span>运行电脑</span>
            <select v-model="taskForm.target_host_id" :disabled="targetHostsLoading">
              <option value="">主控本机</option>
              <option
                v-for="host in targetHosts"
                :key="host.id"
                :value="String(host.id)"
                :disabled="host.approval_status !== 'approved'"
              >
                {{ host.name }}{{ host.approval_status === 'approved' ? '' : '（不可用）' }}
              </option>
            </select>
            <small>手动和计划均发往此电脑；离线或忙碌时排队。</small>
          </label>
          <PlanFields :form="taskForm" :remote="Boolean(taskForm.target_host_id)" :disabled="saving" :identity="editingTask?.id" @unauthorized="handleSessionExpired" />
        </section>

        <div class="field-grid">
          <div class="field">
            <span>最长运行时间</span>
            <div class="schedule-hint">10 分钟，排队不计时</div>
          </div>
          <div class="field">
            <span>任务状态</span
            ><button
              class="switch-row"
              type="button"
              role="switch"
              :aria-checked="taskForm.enabled"
              @click="taskForm.enabled = !taskForm.enabled"
            >
              <i :class="{ active: taskForm.enabled }"><b></b></i
              ><span>{{ taskForm.enabled ? '已启用' : '暂不启用' }}</span>
            </button>
          </div>
        </div>
        <div class="notification-options">
          <div><strong>飞书通知</strong></div>
          <label><input v-model="taskForm.notify_on_success" type="checkbox" />成功时通知</label>
          <label><input v-model="taskForm.notify_on_failure" type="checkbox" />失败时通知</label>
          <label
            :class="{ disabled: !taskForm.notify_on_failure }"
            title="失败或超时时尝试截取宿主机前台窗口；错误日志始终保存。"
            ><input
              v-model="taskForm.failure_screenshot"
              :disabled="!taskForm.notify_on_failure"
              type="checkbox"
            />失败附带截图</label
          >
        </div>
        <section v-if="editingTask" class="edit-code-versions">
          <div class="edit-code-versions-heading">
            <strong>脚本与版本</strong>
          </div>
          <TaskVersions :key="editingTask.id" :task-id="editingTask.id" />
          <DependencyDetails :app-id="editingTask.app_id" :remote="Boolean(editingTask.target_host_id)" />
        </section>
      </fieldset>
      <template #footer>
        <button class="button ghost" type="button" :disabled="saving" @click="taskModalOpen = false">取消</button
        ><button class="button primary" type="button" :disabled="saving" @click="saveTask">
          {{ saving ? '正在保存…' : '保存' }}
        </button>
      </template>
  </WorkflowDialog>
</template>

<style scoped>
.task-edit-fields { display: grid; gap: 16px; border: 0; padding: 0; margin: 0; min-width: 0; }
.task-edit-fields .schedule-card { padding: 16px 0; border: 0; border-block: 1px solid var(--border-soft); border-radius: 0; }
.task-edit-fields :deep(.edit-code-versions) { min-width: 0; }
</style>
