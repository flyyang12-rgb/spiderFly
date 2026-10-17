<script setup>
import { onActivated, onDeactivated } from 'vue'
import { useWorkspaceContext } from '../../workspace/context'
import FilterSection from '../../components/FilterSection.vue'

const {
  applyExecutionFilters,
  executionFilters,
  executionFiltersActive,
  executionHistory,
  executionHistoryEnd,
  executionHistoryLoading,
  executionHistoryError,
  executionHistoryPage,
  executionHistoryStart,
  executionHistoryTotal,
  executionHistoryTotalPages,
  formatDuration,
  formatTime,
  goToExecutionPage,
  loadExecutionHistory,
  navigateTo,
  notificationLabel,
  openExecution,
  requesterName,
  resetExecutionFilters,
  sourceLabel,
  statusLabel,
  tasks,
  view,
  suspendExecutionHistory,
} = useWorkspaceContext()
let suspended = false
onDeactivated(() => { suspended = true; suspendExecutionHistory?.() })
onActivated(() => { if (suspended) { suspended = false; loadExecutionHistory() } })
</script>

<template>
  <section class="view-stack">
    <section class="panel table-panel" aria-label="任务执行记录">
      <header class="panel-heading">
        <div class="history-heading">
<p class="history-count" role="status">{{ executionHistoryError ? '记录尚未完整载入' : executionHistoryLoading ? '正在查询记录…' : executionHistoryTotal + ' 条任务执行' }}</p>
          <slot name="scope-help" />
        </div>
        <button
          class="button secondary compact"
          type="button"
          :disabled="executionHistoryLoading"
          @click="loadExecutionHistory()"
        >
          刷新
        </button>
      </header>
      <FilterSection :active="executionFiltersActive">
      <form class="filter-bar execution-filter-bar history-filter-bar" @submit.prevent="applyExecutionFilters">
        <slot name="scope" />
        <label class="filter-field"
          ><span>任务名称</span
          ><input
            v-model="executionFilters.task_name"
            type="search"
            maxlength="100"
            placeholder="输入任务名称"
        /></label>
        <label class="filter-field"
          ><span>运行状态</span
          ><select v-model="executionFilters.status">
            <option value="all">全部状态</option>
            <option value="pending">排队中</option>
            <option value="running">运行中</option>
            <option value="success">成功</option>
            <option value="failed">失败</option>
            <option value="timeout">超时</option>
            <option value="cancelled">已取消</option>
          </select></label
        >
        <label class="filter-field"
          ><span>发起人</span
          ><input v-model="executionFilters.requester" type="search" maxlength="100" placeholder="输入发起人"
        /></label>
        <div class="history-date-range">
        <label class="filter-field"
          ><span>开始日期</span><input v-model="executionFilters.date_from" type="date"
        /></label>
        <label class="filter-field"
          ><span>结束日期</span><input v-model="executionFilters.date_to" type="date"
        /></label>
        </div>
        <div class="filter-actions execution-filter-actions">
          <button class="button primary compact" type="submit" :disabled="executionHistoryLoading">
            {{ executionHistoryLoading ? '查询中…' : '查询' }}
          </button>
          <button
            class="button secondary compact"
            type="button"
            :disabled="executionHistoryLoading"
            @click="resetExecutionFilters"
          >
            清空筛选
          </button>
        </div>
      </form>
      </FilterSection>
      <div v-if="executionHistoryError" class="notice warning execution-history-error" role="alert">
        <span>{{ executionHistoryError }}{{ executionHistory.length ? '；下方保留上次载入的记录。' : '' }}</span>
        <button class="text-button" type="button" :disabled="executionHistoryLoading" @click="loadExecutionHistory()">重试读取</button>
      </div>
      <div v-if="executionHistory.length" class="table-wrap">
        <table class="execution-history-table">
          <thead>
            <tr>
              <th>状态</th>
              <th>任务</th>
              <th>发起人</th>
              <th>触发来源</th>
              <th>提交时间</th>
              <th>完成时间</th>
              <th>耗时</th>
              <th>通知</th>
              <th class="align-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in executionHistory"
              :key="item.id"
              class="clickable-row"
              tabindex="0"
              @click="openExecution(item)"
              @keydown.enter="openExecution(item)"
            >
              <td>
                <span class="status-badge"
                  ><i class="status-dot" :class="item.status"></i>{{ statusLabel(item.status) }}</span
                >
              </td>
              <td>
                <div class="primary-cell">
                  <strong>{{ item.task_name }}</strong
                  ><small>#{{ item.id }}</small>
                </div>
              </td>
              <td>
                <span class="person-chip">{{ requesterName(item).slice(0, 1) }}</span
                >{{ requesterName(item) }}
              </td>
              <td>{{ sourceLabel(item.trigger_source) }}</td>
              <td class="history-time"><span v-for="(part, index) in formatTime(item.created_at).split(' ')" :key="index">{{ part }}</span></td>
              <td class="history-time"><span v-for="(part, index) in formatTime(item.ended_at || item.finished_at || item.completed_at).split(' ')" :key="index">{{ part }}</span></td>
              <td>{{ formatDuration(item.duration_ms) }}</td>
              <td>{{ notificationLabel(item.notification_status) }}</td>
              <td class="align-right">
                <button class="text-button" type="button" @click.stop="openExecution(item)">查看详情</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else-if="executionHistoryLoading" class="empty-state compact-empty">
        <strong>正在读取运行记录…</strong>
      </div>
      <div v-else-if="executionHistoryError" class="empty-state compact-empty">
        <strong>暂时无法读取运行记录</strong>

      </div>
      <div v-else-if="executionFiltersActive" class="empty-state compact-empty">
        <strong>没有匹配的运行记录</strong>

        <button class="button secondary" type="button" @click="resetExecutionFilters">清空筛选</button>
      </div>
      <div v-else class="empty-state">
        <strong>还没有运行记录</strong>

        <button class="button secondary" type="button" @click="navigateTo('tasks')">打开任务中心</button>
      </div>
      <footer v-if="executionHistoryTotal" class="pagination-bar">
        <span
          >第 {{ executionHistoryStart }}–{{ executionHistoryEnd }} 条，共
          {{ executionHistoryTotal }} 条</span
        >
        <div>
          <button
            class="button secondary compact"
            type="button"
            :disabled="executionHistoryPage <= 1 || executionHistoryLoading"
            @click="goToExecutionPage(executionHistoryPage - 1)"
          >
            上一页
          </button>
          <strong>{{ executionHistoryPage }} / {{ executionHistoryTotalPages }}</strong>
          <button
            class="button secondary compact"
            type="button"
            :disabled="executionHistoryPage >= executionHistoryTotalPages || executionHistoryLoading"
            @click="goToExecutionPage(executionHistoryPage + 1)"
          >
            下一页
          </button>
        </div>
      </footer>
    </section>
  </section>
</template>

<style scoped>
.history-count { margin: 0; color: var(--muted); font-size: 12px; }
.view-stack, .table-panel { min-width: 0; max-width: 100%; }
.execution-history-error { margin: 0 18px 16px; flex-wrap: wrap; overflow-wrap: anywhere; }
.execution-history-error > span { min-width: 0; }
.table-wrap { min-width: 0; width: 100%; max-width: 100%; overflow-x: auto; overscroll-behavior-x: contain; }
.execution-history-table { min-width: 900px; table-layout: auto; }
.execution-history-table td:nth-child(1), .execution-history-table td:nth-child(4), .execution-history-table td:nth-child(7), .execution-history-table td:nth-child(8), .execution-history-table td:nth-child(9) { white-space: nowrap; overflow-wrap: normal; word-break: normal; }
.execution-history-table .history-time > span { display: block; white-space: nowrap; overflow-wrap: normal; word-break: normal; }
.execution-history-table .primary-cell strong { overflow-wrap: anywhere; }
@media (max-width: 720px) { .panel-heading, .pagination-bar { flex-wrap: wrap; } }
</style>
