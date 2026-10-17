<script setup>
import { useWorkspaceContext } from '../../workspace/context'

const {
  isAdmin,
  loadAll,
  navigateTo,
  openExecution,
  orderedActiveExecutions,
  queuedExecutions,
  requesterName,
  runningExecution,
  tasks,
} = useWorkspaceContext()
</script>

<template>
  <section class="panel runtime-active-panel" aria-label="当前运行与排队">
    <header class="panel-heading">
      <div>
        <p>
          {{ runningExecution ? '1 项运行中' : '空闲' }} · {{ queuedExecutions.length }} 项排队
        </p>
      </div>
      <button
        class="button secondary compact"
        type="button"
        @click="loadAll({ quiet: true, includeAdmin: false })"
      >
        刷新
      </button>
    </header>
    <div v-if="orderedActiveExecutions.length" class="queue-list">
      <button
        v-for="item in orderedActiveExecutions"
        :key="item.id"
        type="button"
        class="queue-row"
        :class="{ current: item.status === 'running' }"
        @click="openExecution(item)"
      >
        <span class="queue-number"
          ><i v-if="item.status === 'running'" class="status-dot running"></i
          ><template v-else>{{ item.queue_position || '·' }}</template></span
        >
        <span class="run-copy"
          ><strong>{{ item.task_name }}</strong
          ><small
            >{{ item.status === 'running' ? '运行中' : '排队第 ' + (item.queue_position || '—') + ' 位' }} ·
            #{{ item.id }}</small
          ></span
        >
        <span class="run-result"
          ><strong>{{ item.status === 'running' ? '正在运行' : '排队中' }}</strong
          ><small>{{ requesterName(item) }} 发起</small></span
        >
      </button>
    </div>
    <div v-else class="empty-state">
      <strong>队列空闲</strong
      ><button class="button secondary" type="button" @click="navigateTo('tasks')">打开任务中心</button>
    </div>
  </section>
</template>
