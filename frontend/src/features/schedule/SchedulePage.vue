<script setup>
import { ref } from 'vue'
import { useWorkspaceContext } from '../../workspace/context'
import CronEntryDetails from './CronEntryDetails.vue'
import PlanEditor from './PlanEditor.vue'
import WorkflowRunViewer from './WorkflowRunViewer.vue'
import WorkflowPlanDetails from './WorkflowPlanDetails.vue'
const planOpen = ref(false)
const runId = ref(null)

const {
  formatClock,
  cronScheduleError,
  cronScheduleHasMore,
  cronScheduleLoading,
  loadCronSchedule,
  loadAll,
  manualTasks,
  openExecution,
  scheduleEntryState,
  scheduleEntryStateLabel,
  scheduleScope,
  taskScheduleDays,
  isAdmin,
} = useWorkspaceContext()
</script>

<template>
  <section class="view-stack">
    <section class="panel task-schedule-panel">
      <header class="panel-heading schedule-panel-heading">
        <div>
          <p>北京时间</p>
        </div>
        <div class="schedule-heading-actions">
          <button v-if="isAdmin" class="button primary compact" type="button" @click="planOpen = true">设置定时</button>
          <div class="schedule-scope-switch" role="group" aria-label="时间范围">
            <button
              type="button"
              :class="{ active: scheduleScope === 'today' }"
              @click="scheduleScope = 'today'"
            >
              今日
            </button>
            <button
              type="button"
              :class="{ active: scheduleScope === 'week' }"
              @click="scheduleScope = 'week'"
            >
              本周
            </button>
          </div>
          <button
            class="button secondary compact"
            type="button"
            @click="loadAll({ quiet: true, includeAdmin: false })"
          >
            刷新
          </button>
        </div>
      </header>
      <div v-if="cronScheduleError" class="schedule-feedback" role="alert">
        <span>定时计划载入失败：{{ cronScheduleError }}。已有数据可能未更新。</span>
        <button class="text-button" type="button" @click="loadCronSchedule">重试</button>
      </div>
      <div v-else-if="cronScheduleHasMore" class="schedule-feedback" role="status">定时计划点较多，当前只显示部分。{{ scheduleScope === 'week' ? '可切换到“今日”缩小范围。' : '' }}</div>
      <div v-if="cronScheduleLoading" class="schedule-loading" role="status">正在更新定时计划…</div>
      <div v-if="scheduleScope === 'today'" class="schedule-today-view">
        <header class="schedule-date-heading">
          <div>
            <strong>{{ taskScheduleDays[0]?.fullDateLabel }}</strong>
          </div>
          <small>{{ taskScheduleDays[0]?.entries.length || 0 }} 个计划点</small>
        </header>
        <div v-if="taskScheduleDays[0]?.entries.length" class="schedule-agenda">
          <article
            v-for="entry in taskScheduleDays[0].entries"
            :key="(entry.object_type || 'task') + '-' + entry.task.id + '-' + entry.plannedAt"
            class="schedule-agenda-row"
            :class="'state-' + scheduleEntryState(entry)"
          >
            <time>{{ formatClock(entry.plannedAt) }}</time>
            <span class="schedule-agenda-marker" aria-hidden="true"><i></i></span>
            <div
              class="schedule-entry-card"
              :class="{ clickable: entry.execution && entry.task.trigger_type !== 'cron', 'cron-entry-card': entry.task.trigger_type === 'cron' || entry.object_type === 'workflow' }"
              :role="entry.execution && entry.task.trigger_type !== 'cron' ? 'button' : undefined"
              :tabindex="entry.execution && entry.task.trigger_type !== 'cron' ? 0 : undefined"
              @click="entry.task.trigger_type !== 'cron' && entry.execution && openExecution(entry.execution)"
              @keydown.enter="entry.task.trigger_type !== 'cron' && entry.execution && openExecution(entry.execution)"
            >
              <span
                ><strong>{{ entry.task.name }}</strong
                ><small>{{ entry.object_type === 'workflow' ? '工作流 · ' : '任务 · ' }}{{ entry.task.trigger_type === 'cron' ? 'Cron' : entry.task.trigger_type === 'daily' ? '每日' : '每周' }}</small></span
              >
              <span class="schedule-state"><i></i>{{ scheduleEntryStateLabel(entry) }}</span>
              <WorkflowPlanDetails v-if="entry.object_type === 'workflow'" :entry="entry" @run="id => runId = id" />
              <CronEntryDetails v-else-if="entry.task.trigger_type === 'cron'" :entry="entry" />
            </div>
          </article>
        </div>
        <div v-else class="schedule-empty">
          <strong>{{ cronScheduleLoading ? '正在载入计划' : cronScheduleError ? 'Cron 计划暂未载入' : '今天没有定时计划' }}</strong>
        </div>
      </div>

      <div v-else class="schedule-week-scroll">
        <div class="schedule-week-grid">
          <section
            v-for="day in taskScheduleDays"
            :key="day.dateKey"
            class="schedule-week-day"
            :class="{ today: day.isToday }"
          >
            <header>
              <span>周{{ day.weekdayLabel }}</span
              ><strong>{{ day.dateLabel }}</strong>
            </header>
            <div v-if="day.entries.length" class="schedule-week-list">
              <article
                v-for="entry in day.entries"
                :key="(entry.object_type || 'task') + '-' + entry.task.id + '-' + entry.plannedAt"
                class="schedule-week-entry"
                :class="['state-' + scheduleEntryState(entry), { clickable: entry.execution && entry.task.trigger_type !== 'cron' }]"
                :role="entry.execution && entry.task.trigger_type !== 'cron' ? 'button' : undefined"
                :tabindex="entry.execution && entry.task.trigger_type !== 'cron' ? 0 : undefined"
                @click="entry.task.trigger_type !== 'cron' && entry.execution && openExecution(entry.execution)"
                @keydown.enter="entry.task.trigger_type !== 'cron' && entry.execution && openExecution(entry.execution)"
              >
                <time>{{ formatClock(entry.plannedAt) }}</time>
                <strong>{{ entry.task.name }}</strong>
                <small>{{ entry.object_type === 'workflow' ? '工作流' : '任务' }}</small>
                <small
                  ><span class="schedule-state"><i></i>{{ scheduleEntryStateLabel(entry) }}</span></small
                >
                <WorkflowPlanDetails v-if="entry.object_type === 'workflow'" :entry="entry" @run="id => runId = id" />
                <CronEntryDetails v-else-if="entry.task.trigger_type === 'cron'" :entry="entry" />
              </article>
            </div>
            <div v-else class="schedule-week-empty">{{ cronScheduleLoading ? '正在载入' : cronScheduleError ? 'Cron 暂未载入' : '无定时计划' }}</div>
          </section>
        </div>
      </div>
    </section>
    <PlanEditor v-if="planOpen" @close="planOpen = false" @saved="loadCronSchedule" />
    <WorkflowRunViewer v-if="runId" :run-id="runId" @close="runId = null" />
  </section>
</template>

<style scoped>
.schedule-feedback { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; padding: 10px 18px; background: var(--yellow-soft); color: #756700; font-size: 11px; overflow-wrap: anywhere; }
.schedule-loading { padding: 7px 18px; color: var(--muted); font-size: 11px; }
.cron-entry-card { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px 12px; }
.cron-entry-card > .cron-entry-details { grid-column: 1 / -1; }
.state-maintenance .schedule-state i, .state-unsettled .schedule-state i, .state-waiting .schedule-state i { border-color: #c0a95e; background: var(--yellow-soft); }
.state-skipped .schedule-state i { border-color: var(--faint); }
.state-error .schedule-state i { border-color: var(--coral); background: var(--coral); }
</style>
