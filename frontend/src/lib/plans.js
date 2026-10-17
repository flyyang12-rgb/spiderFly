import { triggerConfig } from './format'

export function emptyPlan() {
  return { trigger_type: 'manual', daily_time: '09:00', weekly_time: '09:00', weekly_days: [1], cron_expression: '0 9 * * *', saved_trigger_type: 'daily' }
}

export function readPlan(item, workflow = false) {
  const config = item?.trigger_config || {}
  const type = item?.trigger_type || 'manual'
  return { ...emptyPlan(), trigger_type: workflow && !item?.schedule_enabled ? 'manual' : type,
    saved_trigger_type: type === 'manual' ? 'daily' : type,
    daily_time: config.time || '09:00', weekly_time: config.time || '09:00',
    weekly_days: [...(config.weekdays || [1])], cron_expression: config.expression || '0 9 * * *' }
}

export function planBody(form, workflow = false) {
  return { trigger_type: form.trigger_type, trigger_config: triggerConfig(form),
    ...(workflow ? { schedule_enabled: form.trigger_type !== 'manual' } : {}) }
}

export function validatePlan(form, remote = false) {
  if (form.trigger_type === 'weekly' && !form.weekly_days.length) return '请至少选择一个星期'
  if (form.trigger_type === 'cron' && remote) return 'Cron 仅支持主控本机，请修改运行电脑'
  if (form.trigger_type === 'cron' && !form.cron_expression.trim()) return '请填写 Cron 表达式'
  return ''
}

export function creationRequestId() {
  return 'create-' + [...crypto.getRandomValues(new Uint8Array(16))].map(byte => byte.toString(16).padStart(2, '0')).join('')
}
