<script setup>
import { useWorkspaceContext } from '../../workspace/context'

const {
  applyExecutionFilters,
  executionFilters,
  executionFiltersActive,
  executionHistory,
  executionHistoryEnd,
  executionHistoryLoading,
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
} = useWorkspaceContext()
</script>

<template>
  <section class="view-stack">
    <div class="section-summary record-summary">
      <span>{{ executionHistoryTotal }} 条记录</span>
    </div>
    <section class="panel scheduler-toolbar">
      <form class="filter-bar execution-filter-bar" @submit.prevent="applyExecutionFilters">
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
        <label class="filter-field"
          ><span>开始日期</span><input v-model="executionFilters.date_from" type="date"
        /></label>
        <label class="filter-field"
          ><span>结束日期</span><input v-model="executionFilters.date_to" type="date"
        /></label>
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
            重置
          </button>
        </div>
      </form>
    </section>
    <section class="panel table-panel">
      <header class="panel-heading">
        <div>
          <h2>运行记录</h2>
          <p>每页 10 条</p>
        </div>
        <button
          class="button secondary compact"
          type="button"
          :disabled="executionHistoryLoading"
          @click="loadExecutionHistory()"
        >
          刷新记录
        </button>
      </header>
      <div v-if="executionHistory.length" class="table-wrap">
        <table>
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
                  ><small>运行编号 #{{ item.id }}</small>
                </div>
              </td>
              <td>
                <span class="person-chip">{{ requesterName(item).slice(0, 1) }}</span
                >{{ requesterName(item) }}
              </td>
              <td>{{ sourceLabel(item.trigger_source) }}</td>
              <td>{{ formatTime(item.created_at) }}</td>
              <td>{{ formatTime(item.ended_at || item.finished_at || item.completed_at) }}</td>
              <td>{{ formatDuration(item.duration_ms) }}</td>
              <td>{{ notificationLabel(item.notification_status) }}</td>
              <td class="align-right">
                <button class="text-button" type="button" @click.stop="openExecution(item)">查看日志</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else-if="executionHistoryLoading" class="empty-state compact-empty">
        <strong>正在读取运行记录…</strong>
      </div>
      <div v-else-if="executionFiltersActive" class="empty-state compact-empty">
        <strong>没有匹配的运行记录</strong>
        <p>调整筛选条件后再试。</p>
        <button class="button secondary" type="button" @click="resetExecutionFilters">清空筛选</button>
      </div>
      <div v-else class="empty-state">
        <strong>还没有运行记录</strong>
        <p>提交运行后即可在这里查看进度。</p>
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
