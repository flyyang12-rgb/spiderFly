import { computed, reactive, ref } from 'vue'
import {
  statusLabel,
  timestampOf,
  shanghaiDateKey,
  dateKeyParts,
  addDaysToDateKey,
  isoWeekday,
  scheduleTimestamp,
} from '../../lib/format'
import { weekdayOptions } from '../../lib/constants'

export function createScheduleState() {
  const scheduleScope = ref('today')
  return { scheduleScope }
}

export function useSchedule({ executions, scheduleScope, tasks }) {
  const taskScheduleDays = computed(() => {
    const todayKey = shanghaiDateKey(Date.now())
    if (scheduleScope.value === 'today') return [buildScheduleDay(todayKey)]
    const weekStartKey = addDaysToDateKey(todayKey, 1 - isoWeekday(todayKey))
    return Array.from({ length: 7 }, (_, index) => buildScheduleDay(addDaysToDateKey(weekStartKey, index)))
  })

  function scheduleExecution(taskId, dateKey) {
    return (
      executions.value
        .filter(
          (item) =>
            Number(item.task_id) === Number(taskId) &&
            item.trigger_source === 'schedule' &&
            shanghaiDateKey(item.created_at || item.started_at) === dateKey,
        )
        .sort(
          (a, b) => timestampOf(b.created_at || b.started_at) - timestampOf(a.created_at || a.started_at),
        )[0] || null
    )
  }

  function buildScheduleDay(dateKey) {
    const weekday = isoWeekday(dateKey)
    const entries = tasks.value
      .flatMap((task) => {
        if (!task.enabled || !['daily', 'weekly'].includes(task.trigger_type)) return []
        const config = task.trigger_config || {}
        if (task.trigger_type === 'weekly' && !(config.weekdays || []).map(Number).includes(weekday))
          return []
        if (!/^\d{2}:\d{2}$/.test(config.time || '')) return []
        const plannedTimestamp = scheduleTimestamp(dateKey, config.time)
        if (!plannedTimestamp) return []
        const execution = scheduleExecution(task.id, dateKey)
        if (!execution && timestampOf(task.created_at) > plannedTimestamp) return []
        return [
          {
            task,
            dateKey,
            plannedTimestamp,
            plannedAt: new Date(plannedTimestamp).toISOString(),
            execution,
          },
        ]
      })
      .sort(
        (a, b) =>
          a.plannedTimestamp - b.plannedTimestamp ||
          String(a.task.name).localeCompare(String(b.task.name), 'zh-CN'),
      )
    const { year, month, day } = dateKeyParts(dateKey)
    return {
      dateKey,
      weekday,
      weekdayLabel: weekdayOptions.find((item) => item.value === weekday)?.label || '',
      dateLabel: `${month}月${day}日`,
      fullDateLabel: `${year}年${month}月${day}日`,
      isToday: dateKey === shanghaiDateKey(Date.now()),
      entries,
    }
  }

  function scheduleEntryState(entry) {
    if (entry.execution) return entry.execution.status
    return entry.plannedTimestamp > Date.now() ? 'scheduled' : 'missed'
  }

  function scheduleEntryStateLabel(entry) {
    const state = scheduleEntryState(entry)
    if (state === 'scheduled') return '待执行'
    if (state === 'missed') return '未触发'
    return statusLabel(state)
  }

  return {
    taskScheduleDays,
    scheduleExecution,
    buildScheduleDay,
    scheduleEntryState,
    scheduleEntryStateLabel,
  }
}
