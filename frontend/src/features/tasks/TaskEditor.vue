<script setup>
import { useWorkspaceContext } from '../../workspace/context'
import TaskVersions from '../../TaskVersions.vue'

const {
  editingTask,
  isAdmin,
  saveTask,
  saving,
  taskForm,
  taskModalOpen,
  targetHosts,
  targetHostsLoading,
  toggleWeekday,
  triggerOptions,
  weekdayOptions,
} = useWorkspaceContext()
</script>

<template>
  <div class="modal-layer" @mousedown.self="taskModalOpen = false">
    <section class="modal plan-modal" role="dialog" aria-modal="true" aria-label="编辑任务">
      <header>
        <div><h2>编辑任务</h2></div>
        <button class="modal-close" type="button" aria-label="关闭" @click="taskModalOpen = false">×</button>
      </header>
      <div class="modal-body">
        <label class="field"
          ><span>任务名称</span
          ><input v-model="taskForm.name" type="text" maxlength="100" placeholder="例如：订单同步"
        /></label>
        <label class="field"
          ><span>任务说明</span
          ><input v-model="taskForm.description" type="text" maxlength="500" placeholder="说明任务用途"
        /></label>

        <section class="schedule-card">
          <div class="schedule-card-heading">
            <div><strong>触发方式</strong></div>
            <span>北京时间 UTC+8</span>
          </div>
          <label v-if="isAdmin" class="field schedule-target-field">
            <span>默认运行电脑（手动和计划）</span>
            <select v-model="taskForm.target_host_id" :disabled="targetHostsLoading">
              <option value="">主控本机 A（旧执行链路）</option>
              <option
                v-for="host in targetHosts"
                :key="host.id"
                :value="String(host.id)"
                :disabled="host.approval_status !== 'approved'"
              >
                {{ host.name }}{{ host.approval_status === 'approved' ? '' : '（不可用）' }}
              </option>
            </select>
            <small>手动和计划运行都发往这台电脑；离线或忙碌时在原电脑排队。</small>
          </label>
          <div class="trigger-choice-grid">
            <button
              v-for="option in triggerOptions"
              :key="option.value"
              type="button"
              :class="{ active: taskForm.trigger_type === option.value }"
              @click="taskForm.trigger_type = option.value"
            >
              <i></i>{{ option.label }}
            </button>
          </div>
          <div v-if="taskForm.trigger_type === 'manual'" class="schedule-hint">手动点击“运行”执行</div>
          <label v-else-if="taskForm.trigger_type === 'daily'" class="field"
            ><span>每天执行时间</span><input v-model="taskForm.daily_time" type="time"
          /></label>
          <div v-else class="weekly-fields">
            <div class="field">
              <span>执行星期</span>
              <div class="weekday-grid">
                <button
                  v-for="day in weekdayOptions"
                  :key="day.value"
                  type="button"
                  :class="{ active: taskForm.weekly_days.includes(day.value) }"
                  @click="toggleWeekday(day.value)"
                >
                  周{{ day.label }}
                </button>
              </div>
            </div>
            <label class="field"
              ><span>执行时间</span><input v-model="taskForm.weekly_time" type="time"
            /></label>
          </div>
        </section>

        <div class="field-grid">
          <div class="field">
            <span>最长运行时间</span>
            <div class="schedule-hint">10 分钟，排队不计时</div>
          </div>
          <div class="field">
            <span>计划状态</span
            ><button
              class="switch-row"
              type="button"
              role="switch"
              :aria-checked="taskForm.enabled"
              @click="taskForm.enabled = !taskForm.enabled"
            >
              <i :class="{ active: taskForm.enabled }"><b></b></i
              ><span>{{ taskForm.enabled ? '保存后立即启用' : '暂不启用' }}</span>
            </button>
          </div>
        </div>
        <div class="notification-options">
          <div><strong>最终通知</strong><small>每次结束通知一次</small></div>
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
            <strong>脚本与版本</strong><small>上传新脚本、切换当前版本或下载历史代码</small>
          </div>
          <TaskVersions :key="editingTask.id" :task-id="editingTask.id" />
        </section>
      </div>
      <footer>
        <button class="button ghost" type="button" @click="taskModalOpen = false">取消</button
        ><button class="button primary" type="button" :disabled="saving" @click="saveTask">
          {{ saving ? '正在保存…' : '保存修改' }}
        </button>
      </footer>
    </section>
  </div>
</template>
