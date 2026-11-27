<script setup>
import { useWorkspaceContext } from '../workspace/context'

const {
  activeExecutions,
  attentionTasks,
  buildingTasks,
  formatDuration,
  formatTime,
  navigateTo,
  openExecution,
  queuedExecutions,
  readyTasks,
  recentExecutions,
  requesterName,
  runningExecution,
  statusLabel,
  tasks,
} = useWorkspaceContext()
</script>

<template>
  <section class="view-stack overview-page">
    <div class="metric-grid overview-metrics">
      <article class="metric-card">
        <span>任务总数</span><strong>{{ tasks.length }}</strong
        ><small>{{ tasks.filter((item) => item.enabled).length }} 个已启用</small>
      </article>
      <article class="metric-card">
        <span>环境就绪</span><strong>{{ readyTasks.length }}</strong
        ><small v-if="buildingTasks.length">{{ buildingTasks.length }} 个准备中</small>
      </article>
      <article class="metric-card featured">
        <span>运行队列</span
        ><strong :class="{ 'queue-text': activeExecutions.length }">{{ activeExecutions.length }}</strong
        ><small>{{
          runningExecution ? '1 个运行，' + queuedExecutions.length + ' 个等待' : queuedExecutions.length ? queuedExecutions.length + ' 个等待' : '暂无运行'
        }}</small>
      </article>
    </div>

    <div v-if="attentionTasks.length" class="notice warning">
      <span class="notice-icon">!</span>
      <div>
        <strong>{{ attentionTasks.length }} 个任务最近运行异常</strong>
      </div>
      <button class="button ghost compact" type="button" @click="navigateTo('runtime', 'executions')">
        查看记录
      </button>
    </div>

    <div class="two-column queue-overview">
      <section class="panel overview-panel">
        <header class="panel-heading">
          <div><h2>当前队列</h2></div>
          <button class="text-button" type="button" @click="navigateTo('runtime', 'active')">
            查看队列
          </button>
        </header>
        <div v-if="runningExecution || queuedExecutions.length" class="queue-list">
          <button
            v-if="runningExecution"
            type="button"
            class="queue-row current"
            @click="openExecution(runningExecution)"
          >
            <span class="queue-number"><i class="status-dot running"></i></span>
            <span class="run-copy"
              ><strong>{{ runningExecution.task_name }}</strong
              ><small
                >{{ requesterName(runningExecution) }} 发起 ·
                {{ formatTime(runningExecution.started_at) }}</small
              ></span
            >
            <span class="run-result"
              ><strong>正在运行</strong
              ><small>{{ formatDuration(runningExecution.duration_ms) }}</small></span
            >
          </button>
          <button
            v-for="item in queuedExecutions.slice(0, 5)"
            :key="item.id"
            type="button"
            class="queue-row"
            @click="openExecution(item)"
          >
            <span class="queue-number">{{ item.queue_position || '·' }}</span>
            <span class="run-copy"
              ><strong>{{ item.task_name }}</strong
              ><small>{{ requesterName(item) }} 发起 · {{ formatTime(item.created_at) }}</small></span
            >
            <span class="run-result"
              ><strong>排队中</strong
              ><small>{{ item.error_message || '第 ' + (item.queue_position || '—') + ' 位' }}</small></span
            >
          </button>
        </div>
        <div v-else class="empty-state compact-empty">
          <strong>暂无排队任务</strong>
        </div>
      </section>

      <section class="panel overview-panel">
        <header class="panel-heading">
          <div><h2>最近运行</h2></div>
          <button class="text-button" type="button" @click="navigateTo('runtime', 'executions')">
            全部记录
          </button>
        </header>
        <div v-if="recentExecutions.length" class="run-list">
          <button
            v-for="item in recentExecutions.slice(0, 5)"
            :key="item.id"
            type="button"
            @click="openExecution(item)"
          >
            <span class="status-dot" :class="item.status"></span>
            <span class="run-copy"
              ><strong>{{ item.task_name }}</strong
              ><small>{{ requesterName(item) }} · {{ formatTime(item.created_at) }}</small></span
            >
            <span class="run-result"
              ><strong>{{ statusLabel(item.status) }}</strong
              ><small>{{ formatDuration(item.duration_ms) }}</small></span
            >
          </button>
        </div>
        <div v-else class="empty-state compact-empty"><strong>暂无运行记录</strong></div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.overview-page { gap: 20px; }
.overview-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.overview-metrics .metric-card { display: flex; min-height: 124px; flex-direction: column; align-items: flex-start; gap: 4px; padding: 18px 20px; }
.overview-metrics .metric-card > span { color: var(--muted); font-size: 14px; }
.overview-metrics .metric-card > strong { margin: 0; font-size: 32px; font-weight: 500; line-height: 1.3; font-variant-numeric: tabular-nums; }
.overview-metrics .metric-card small { line-height: 20px; }
.queue-overview { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 20px; align-items: stretch; }
.overview-panel .panel-heading { min-height: 64px; padding: 14px 20px; }
.overview-panel .panel-heading h2 { font-size: 18px; font-weight: 600; }
.overview-panel .text-button { min-height: var(--control-row-height); padding: 4px 10px; }
.overview-panel .run-list > button { min-height: 72px; padding: 14px 20px; gap: 12px; }
.overview-panel .run-copy { gap: 4px; }
.overview-panel .run-copy strong { color: var(--text); font-size: 15px; font-weight: 400; line-height: 1.6; }
.overview-panel .run-result { gap: 4px; }
.overview-panel .run-result strong { color: var(--muted); font-size: 14px; font-weight: 400; line-height: 24px; }
.overview-panel .run-copy small, .overview-panel .run-result small { font-size: 13px; line-height: 20px; }
.overview-panel .run-list .run-copy strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overview-panel .compact-empty { min-height: 260px; height: calc(100% - 64px); }
.overview-panel .compact-empty strong { color: var(--muted); font-size: 14px; font-weight: 400; }
@media (max-width: 1100px) { .queue-overview { grid-template-columns: minmax(0, 1fr); } .overview-panel .compact-empty { height: auto; min-height: 180px; } }

@media (max-width: 720px) {
  .overview-metrics { grid-template-columns: 1fr; gap: 12px; }
  .overview-metrics .metric-card { min-height: 112px; padding: 16px 18px; }
  .overview-panel .run-list > button { padding: 12px 16px; }
  .overview-panel .run-copy small { font-size: 13px; }
}
</style>
