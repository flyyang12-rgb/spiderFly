<script setup>
import { useWorkspaceContext } from '../workspace/context'

const {
  activeExecutions,
  attentionTasks,
  buildingTasks,
  executions,
  formatDuration,
  formatTime,
  isAdmin,
  navigateTo,
  openExecution,
  overview,
  queuedExecutions,
  readyTasks,
  recentExecutions,
  requesterName,
  runningExecution,
  statusLabel,
  tasks,
  view,
} = useWorkspaceContext()
</script>

<template>
  <section class="view-stack">
    <div class="metric-grid">
      <article class="metric-card">
        <span>任务总数</span><strong>{{ tasks.length }}</strong
        ><small>{{ tasks.filter((item) => item.enabled).length }} 个已启用</small>
      </article>
      <article class="metric-card">
        <span>Python 环境</span><strong>{{ readyTasks.length }}</strong
        ><small>{{ buildingTasks.length ? buildingTasks.length + ' 个准备中' : '已就绪' }}</small>
      </article>
      <article class="metric-card featured">
        <span>运行队列</span
        ><strong :class="{ 'queue-text': activeExecutions.length }">{{ activeExecutions.length }}</strong
        ><small>{{
          runningExecution ? '1 个运行，' + queuedExecutions.length + ' 个等待' : '当前没有运行任务'
        }}</small>
      </article>
      <article class="metric-card">
        <span>运行主机</span><strong>{{ runningExecution ? '忙碌' : '空闲' }}</strong
        ><small>串行执行</small>
      </article>
    </div>

    <nav class="console-path" :class="{ 'operator-path': !isAdmin }" aria-label="常用入口">
      <template v-if="isAdmin">
        <button type="button" @click="navigateTo('management', 'apps')">
          <span>01</span><strong>创建任务</strong><small>上传 Python</small>
        </button>
        <i>→</i>
      </template>
      <button type="button" @click="navigateTo('tasks')">
        <span>{{ isAdmin ? '02' : '01' }}</span
        ><strong>任务中心</strong><small>运行、定时、修复和删除</small>
      </button>
      <i>→</i>
      <button type="button" @click="navigateTo('runtime', 'queue')">
        <span>{{ isAdmin ? '03' : '02' }}</span
        ><strong>任务时间表</strong><small>查看今日和本周计划</small>
      </button>
      <i>→</i>
      <button type="button" @click="navigateTo('runtime', 'executions')">
        <span>{{ isAdmin ? '04' : '03' }}</span
        ><strong>运行记录</strong><small>结果与日志</small>
      </button>
    </nav>

    <div v-if="attentionTasks.length" class="notice warning">
      <span class="notice-icon">!</span>
      <div>
        <strong>{{ attentionTasks.length }} 个任务最近一次运行异常</strong><small>查看日志定位问题</small>
      </div>
      <button class="button ghost compact" type="button" @click="navigateTo('runtime', 'executions')">
        查看记录
      </button>
    </div>

    <div class="two-column queue-overview">
      <section class="panel">
        <header class="panel-heading">
          <div><h2>当前队列</h2></div>
          <button class="text-button" type="button" @click="navigateTo('runtime', 'active')">
            查看运行队列
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
          <strong>共享执行器正在等待</strong>
        </div>
      </section>

      <section class="panel">
        <header class="panel-heading">
          <div><h2>最近完成</h2></div>
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
        <div v-else class="empty-state compact-empty"><strong>还没有完成记录</strong></div>
      </section>
    </div>
  </section>
</template>
