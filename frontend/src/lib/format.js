import { triggerOptions, weekdayOptions } from './constants'

export function triggerConfig(form) {
  if (form.trigger_type === 'daily') return { time: form.daily_time }
  if (form.trigger_type === 'weekly') return { weekdays: form.weekly_days, time: form.weekly_time }
  return {}
}

export function taskOwnerKey(task) {
  const ownerId = Number(task?.created_by)
  return Number.isInteger(ownerId) && ownerId > 0 ? `user:${ownerId}` : 'legacy'
}

export function triggerLabel(type) {
  return triggerOptions.find((item) => item.value === type)?.label || type
}

export function triggerDetail(task) {
  const config = task.trigger_config || {}
  if (task.trigger_type === 'daily') return '每天 ' + config.time
  if (task.trigger_type === 'weekly') {
    const days = (config.weekdays || [])
      .map((day) => weekdayOptions.find((item) => item.value === day)?.label)
      .join('、')
    return '周' + days + ' ' + config.time
  }
  return '按需手动运行'
}

export function sourceLabel(source) {
  return { schedule: '定时调度', maintenance: '修复重跑' }[source] || '手动运行'
}

export function statusLabel(status) {
  return (
    {
      idle: '尚未运行',
      pending: '排队中',
      running: '运行中',
      success: '成功',
      failed: '失败',
      timeout: '超时',
      cancelled: '已取消',
    }[status] ||
    status ||
    '未知'
  )
}

export function businessOutcomeLabel(outcome) {
  return (
    {
      success: '业务成功',
      failure: '业务失败',
      manual_required: '需要人工介入',
    }[outcome] ||
    outcome ||
    '—'
  )
}

export function envStatusLabel(status) {
  return (
    {
      ready: 'Python 就绪',
      building: '准备中',
      failed: '准备失败',
      pending: '准备中',
      not_built: '准备中',
    }[status] ||
    status ||
    '未知'
  )
}

export function notificationLabel(status) {
  return (
    {
      pending: '等待发送',
      sent: '已发送',
      skipped: '未配置',
      disabled: '已关闭',
      failed: '发送失败',
    }[status] ||
    status ||
    '—'
  )
}

export function roleLabel(role) {
  return { super_admin: '超级管理员', admin: '管理员', operator: '普通成员' }[role] || role
}

export function auditActionLabel(action) {
  const map = {
    login: '登录系统',
    login_failed: '登录失败',
    logout: '退出登录',
    change_password: '修改密码',
    update_user: '修改成员',
    delete_user: '删除成员',
    create_task: '创建任务',
    update_task: '修改任务',
    delete_task: '删除任务',
    archive_task: '删除任务',
    run_task: '发起运行',
    cancel_execution: '取消执行',
    force_stop_execution: '强制停止',
    create_app: '创建任务',
    delete_app: '清理旧任务',
    remove_app: '清理旧任务',
    rebuild_app: '修复运行环境',
    rebuild_environment: '修复运行环境',
    create_user: '创建成员',
  }
  return map[action] || action || '系统操作'
}

export function formatDuration(value) {
  if (value == null) return '—'
  const seconds = Math.max(0, Number(value) || 0) / 1000
  const display =
    seconds < 1
      ? seconds.toFixed(3)
      : seconds
          .toFixed(2)
          .replace(/\.00$/, '')
          .replace(/(\.\d)0$/, '$1')
  return display + '秒'
}

export function formatFileSize(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '大小未知'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return size.toLocaleString('zh-CN', { maximumFractionDigits: unit ? 1 : 0 }) + ' ' + units[unit]
}

export function formatTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' }) : '—'
}

export function timestampOf(value) {
  const timestamp = value ? new Date(value).getTime() : 0
  return Number.isFinite(timestamp) ? timestamp : 0
}

export function shanghaiDateKey(value) {
  const timestamp = timestampOf(value)
  if (!timestamp) return ''
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(timestamp))
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}`
}

export function formatClock(value) {
  return value
    ? new Date(value).toLocaleTimeString('zh-CN', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        timeZone: 'Asia/Shanghai',
      })
    : '—'
}

export function dateKeyParts(dateKey) {
  const [year, month, day] = String(dateKey || '')
    .split('-')
    .map(Number)
  return { year, month, day }
}

export function addDaysToDateKey(dateKey, amount) {
  const { year, month, day } = dateKeyParts(dateKey)
  const date = new Date(Date.UTC(year, month - 1, day + amount))
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(date.getUTCDate()).padStart(2, '0')}`
}

export function isoWeekday(dateKey) {
  const { year, month, day } = dateKeyParts(dateKey)
  return new Date(Date.UTC(year, month - 1, day)).getUTCDay() || 7
}

export function scheduleTimestamp(dateKey, time) {
  const { year, month, day } = dateKeyParts(dateKey)
  const [hour, minute] = String(time || '')
    .split(':')
    .map(Number)
  if (![year, month, day, hour, minute].every(Number.isFinite)) return 0
  return Date.UTC(year, month - 1, day, hour - 8, minute)
}

export function requesterName(item) {
  return item.requested_by_name || item.requested_by_username || '系统调度'
}

export function auditActor(item) {
  return item.actor_name || item.user_display_name || item.username || '系统'
}

export function auditTarget(item) {
  if (item.target_name) return item.target_name
  if (item.target_type && item.target_id) return item.target_type + ' #' + item.target_id
  if (item.target_type) return item.target_type
  if (item.resource_type && item.resource_id) return item.resource_type + ' #' + item.resource_id
  if (item.entity_type && item.entity_id) return item.entity_type + ' #' + item.entity_id
  return '—'
}
