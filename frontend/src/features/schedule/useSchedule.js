import { computed, onBeforeUnmount, ref, watch } from 'vue'
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
import { request } from '../../lib/api'

export function createScheduleState() {
  const scheduleScope = ref('today')
  return { scheduleScope }
}

export function useSchedule({ executions, me, runtimeTab, scheduleScope, tasks, view }) {
  const todayKey = ref(shanghaiDateKey(Date.now()))
  const cronScheduleEntries = ref([])
  const cronScheduleLoading = ref(false)
  const cronScheduleError = ref('')
  const cronScheduleHasMore = ref(false)
  let generation = 0
  let controller = null
  let loadedRange = ''

  const dateKeys = computed(() => {
    if (scheduleScope.value === 'today') return [todayKey.value]
    const weekStartKey = addDaysToDateKey(todayKey.value, 1 - isoWeekday(todayKey.value))
    return Array.from({ length: 7 }, (_, index) => addDaysToDateKey(weekStartKey, index))
  })
  const taskScheduleDays = computed(() => dateKeys.value.map(buildScheduleDay))

  async function loadCronSchedule() {
    const requestGeneration = ++generation
    controller?.abort()
    todayKey.value = shanghaiDateKey(Date.now())
    if (!me.value || view.value !== 'runtime' || runtimeTab.value !== 'queue') {
      cronScheduleEntries.value = []
      cronScheduleLoading.value = false
      cronScheduleError.value = ''
      cronScheduleHasMore.value = false
      loadedRange = ''
      return
    }
    const keys = dateKeys.value
    const range = keys[0] + ':' + keys[keys.length - 1]
    if (range !== loadedRange) {
      cronScheduleEntries.value = []
      cronScheduleHasMore.value = false
    }
    cronScheduleLoading.value = true
    cronScheduleError.value = ''
    const params = new URLSearchParams({
      start: new Date(scheduleTimestamp(keys[0], '00:00')).toISOString(),
      end: new Date(scheduleTimestamp(addDaysToDateKey(keys[keys.length - 1], 1), '00:00')).toISOString(),
    })
    controller = new AbortController()
    try {
      const results = await Promise.allSettled([
        request('/schedules/cron?' + params.toString(), { signal: controller.signal }),
        request('/schedules/workflows?' + params.toString(), { signal: controller.signal }),
      ])
      if (requestGeneration !== generation) return
      const successful = results.filter(result => result.status === 'fulfilled').map(result => result.value)
      cronScheduleError.value = results.filter(result => result.status === 'rejected').map(result => result.reason.message).join('；')
      cronScheduleEntries.value = successful.flatMap(result => Array.isArray(result.entries) ? result.entries : []).map((entry) => ({
        ...entry,
        dateKey: shanghaiDateKey(entry.plannedAt),
        plannedTimestamp: timestampOf(entry.plannedAt),
      }))
      cronScheduleHasMore.value = successful.some(result => result.has_more)
      loadedRange = range
    } catch (error) {
      if (requestGeneration !== generation || error.name === 'AbortError') return
      cronScheduleError.value = error.message
    } finally {
      if (requestGeneration === generation) cronScheduleLoading.value = false
    }
  }

  watch([me, view, runtimeTab, scheduleScope, tasks, executions], loadCronSchedule, { immediate: true })
  onBeforeUnmount(() => {
    generation += 1
    controller?.abort()
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
        if (!execution && task.first_environment_ready === 0 && !task.target_host_id) return []
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
      .concat(cronScheduleEntries.value.filter((entry) => entry.dateKey === dateKey))
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
      isToday: dateKey === todayKey.value,
      entries,
    }
  }

  function scheduleEntryState(entry) {
    if (entry.object_type === 'workflow' || entry.task.trigger_type === 'cron') return entry.state || 'waiting'
    if (entry.execution) return entry.execution.status
    return entry.plannedTimestamp > Date.now() ? 'scheduled' : 'missed'
  }

  function scheduleEntryStateLabel(entry) {
    const state = scheduleEntryState(entry)
    if (state === 'scheduled') return '待执行'
    if (state === 'missed') return '未触发'
    if (state === 'skipped') return '已跳过'
    if (state === 'error') return '触发失败'
    if (state === 'waiting') return '等待入队'
    if (state === 'unsettled') return '等待收尾确认'
    if (state === 'maintenance') return '自动维护中'
    if (state === 'settled') return '已结束'
    if (state === 'stopping') return '停止中'
    return statusLabel(state)
  }

  return {
    cronScheduleError,
    cronScheduleHasMore,
    cronScheduleLoading,
    loadCronSchedule,
    taskScheduleDays,
    scheduleExecution,
    buildScheduleDay,
    scheduleEntryState,
    scheduleEntryStateLabel,
  }
}
