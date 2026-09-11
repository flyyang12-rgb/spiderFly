<script setup>
import { useWorkspaceContext } from '../../workspace/context'

const {
  formatClock,
  loadAll,
  manualTasks,
  openExecution,
  scheduleEntryState,
  scheduleEntryStateLabel,
  scheduleScope,
  taskScheduleDays,
  view,
} = useWorkspaceContext()
</script>

<template>
  <section class="view-stack">
    <section class="panel task-schedule-panel">
      <header class="panel-heading schedule-panel-heading">
        <div>
          <h2>任务时间表</h2>
          <p>北京时间 · {{ manualTasks.length }} 个手动任务按需运行</p>
        </div>
        <div class="schedule-heading-actions">
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
      <div v-if="scheduleScope === 'today'" class="schedule-today-view">
        <header class="schedule-date-heading">
          <div>
            <strong>今天</strong><span>{{ taskScheduleDays[0]?.fullDateLabel }}</span>
          </div>
          <small>{{ taskScheduleDays[0]?.entries.length || 0 }} 项自动任务</small>
        </header>
        <div v-if="taskScheduleDays[0]?.entries.length" class="schedule-agenda">
          <article
            v-for="entry in taskScheduleDays[0].entries"
            :key="entry.dateKey + '-' + entry.task.id"
            class="schedule-agenda-row"
            :class="'state-' + scheduleEntryState(entry)"
          >
            <time>{{ formatClock(entry.plannedAt) }}</time>
            <span class="schedule-agenda-marker" aria-hidden="true"><i></i></span>
            <div
              class="schedule-entry-card"
              :class="{ clickable: entry.execution }"
              :role="entry.execution ? 'button' : undefined"
              :tabindex="entry.execution ? 0 : undefined"
              @click="entry.execution && openExecution(entry.execution)"
              @keydown.enter="entry.execution && openExecution(entry.execution)"
            >
              <span
                ><strong>{{ entry.task.name }}</strong
                ><small>{{ entry.task.trigger_type === 'daily' ? '每日任务' : '每周任务' }}</small></span
              >
              <span class="schedule-state"><i></i>{{ scheduleEntryStateLabel(entry) }}</span>
            </div>
          </article>
        </div>
        <div v-else class="schedule-empty">
          <strong>今天没有自动任务</strong><span>手动任务仍可在任务中心按需运行。</span>
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
                :key="entry.dateKey + '-' + entry.task.id"
                class="schedule-week-entry"
                :class="['state-' + scheduleEntryState(entry), { clickable: entry.execution }]"
                :role="entry.execution ? 'button' : undefined"
                :tabindex="entry.execution ? 0 : undefined"
                @click="entry.execution && openExecution(entry.execution)"
                @keydown.enter="entry.execution && openExecution(entry.execution)"
              >
                <time>{{ formatClock(entry.plannedAt) }}</time>
                <strong>{{ entry.task.name }}</strong>
                <small
                  ><span class="schedule-state"><i></i>{{ scheduleEntryStateLabel(entry) }}</span></small
                >
              </article>
            </div>
            <div v-else class="schedule-week-empty">无自动任务</div>
          </section>
        </div>
      </div>
    </section>
  </section>
</template>
