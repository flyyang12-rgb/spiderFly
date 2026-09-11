<script setup>
import { useWorkspaceContext } from '../../workspace/context'

const {
  deletingTask,
  envStatusLabel,
  filteredTasks,
  filters,
  formatTime,
  isAdmin,
  loadAll,
  openAi,
  openEdit,
  ownerOptions,
  rebuildTaskEnvironment,
  resetFilters,
  runTask,
  statusLabel,
  taskIsActive,
  tasks,
  toggleTask,
  triggerDetail,
  triggerLabel,
  triggerOptions,
  view,
  viewTaskExecution,
} = useWorkspaceContext()
</script>

<template>
  <section class="view-stack">
    <section class="panel scheduler-toolbar">
      <div class="filter-bar">
        <label class="filter-field"
          ><span>任务名称</span><input v-model="filters.name" type="search" placeholder="输入任务名称"
        /></label>
        <label class="filter-field"
          ><span>启用状态</span
          ><select v-model="filters.enabled">
            <option value="all">全部状态</option>
            <option value="enabled">已启用</option>
            <option value="disabled">已停用</option>
          </select></label
        >
        <label class="filter-field"
          ><span>触发方式</span
          ><select v-model="filters.trigger_type">
            <option value="all">全部触发方式</option>
            <option v-for="option in triggerOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select></label
        >
        <label class="filter-field"
          ><span>归属人</span
          ><select v-model="filters.owner">
            <option value="all">全部归属人</option>
            <option v-for="owner in ownerOptions" :key="owner.value" :value="owner.value">
              {{ owner.label }}
            </option>
          </select></label
        >
        <div class="filter-actions">
          <button
            class="icon-button toolbar-icon"
            type="button"
            title="刷新任务"
            aria-label="刷新任务"
            @click="loadAll({ quiet: true, includeAdmin: false })"
          >
            ↻
          </button>
          <button
            class="icon-button toolbar-icon"
            type="button"
            title="清空筛选"
            aria-label="清空筛选"
            @click="resetFilters"
          >
            ···
          </button>
        </div>
      </div>
    </section>

    <section class="panel table-panel">
      <header class="panel-heading">
        <div>
          <h2>任务列表</h2>
          <p>{{ filteredTasks.length }} 项</p>
        </div>
      </header>
      <div v-if="filteredTasks.length" class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>任务</th>
              <th>启用</th>
              <th>触发方式</th>
              <th>下次运行</th>
              <th>最近状态</th>
              <th>归属人</th>
              <th class="align-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in filteredTasks" :key="task.id">
              <td>
                <div class="primary-cell">
                  <strong>{{ task.name }}</strong>
                  <small
                    class="inline-state"
                    :class="'env-' + task.environment_status"
                    :title="task.environment_error || ''"
                  >
                    {{ envStatusLabel(task.environment_status)
                    }}<template v-if="task.environment_status === 'failed' && task.environment_error">
                      · {{ task.environment_error }}</template
                    >
                  </small>
                </div>
              </td>
              <td>
                <button
                  class="plan-state"
                  :class="{ enabled: task.enabled }"
                  type="button"
                  @click="toggleTask(task)"
                >
                  <i></i>{{ task.enabled ? '已启用' : '已停用' }}
                </button>
              </td>
              <td>
                <div class="primary-cell">
                  <strong>{{ triggerLabel(task.trigger_type) }}</strong
                  ><small>{{ triggerDetail(task) }}</small>
                </div>
              </td>
              <td>
                <span :class="{ muted: !task.next_run_at }">{{
                  task.enabled ? formatTime(task.next_run_at) : '停用后不调度'
                }}</span>
              </td>
              <td>
                <span class="status-badge"
                  ><i class="status-dot" :class="task.last_status"></i
                  >{{ statusLabel(task.last_status) }}</span
                >
              </td>
              <td>
                <span class="person-chip">{{ (task.created_by_name || '系统迁移').slice(0, 1) }}</span
                >{{ task.created_by_name || '系统迁移' }}
              </td>
              <td class="align-right">
                <div class="row-actions">
                  <button v-if="isAdmin" class="button ghost compact" type="button" @click="openAi(task)">
                    AI 助手
                  </button>
                  <button
                    v-if="taskIsActive(task)"
                    class="button primary compact"
                    type="button"
                    @click="viewTaskExecution(task)"
                  >
                    查看运行
                  </button>
                  <button
                    v-else
                    class="button primary compact"
                    type="button"
                    :disabled="!task.enabled || task.environment_status !== 'ready'"
                    @click="runTask(task)"
                  >
                    运行
                  </button>
                  <button
                    class="icon-button"
                    type="button"
                    :disabled="taskIsActive(task)"
                    aria-label="编辑任务"
                    :title="taskIsActive(task) ? '任务完成后才能编辑' : '编辑任务'"
                    @click="openEdit(task)"
                  >
                    ✎
                  </button>
                  <button
                    v-if="isAdmin && task.environment_status === 'failed'"
                    class="button secondary compact"
                    type="button"
                    @click="rebuildTaskEnvironment(task)"
                  >
                    修复环境
                  </button>
                  <button
                    v-if="isAdmin"
                    class="button ghost compact archive-button"
                    type="button"
                    aria-label="删除任务"
                    title="同时删除程序、独立环境和运行记录"
                    @click="deletingTask = task"
                  >
                    删除
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else-if="tasks.length" class="empty-state">
        <strong>没有匹配的任务</strong>
        <p>调整筛选条件后再试。</p>
        <button class="button secondary" type="button" @click="resetFilters">清空筛选</button>
      </div>
      <div v-else class="empty-state">
        <div class="empty-canopy"><span></span><span></span><span></span></div>
        <strong>还没有任务</strong>
      </div>
    </section>
  </section>
</template>
