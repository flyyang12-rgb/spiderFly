export const baseNavItems = [
  { id: 'overview', label: '工作台', mark: 'W' },
  { id: 'tasks', label: '任务中心', mark: 'T' },
  { id: 'runtime', label: '运行中心', mark: 'R' },
]

export const adminNavItems = [{ id: 'management', label: '管理中心', mark: 'M' }]

export const runtimeTabs = [
  { id: 'active', label: '运行队列' },
  { id: 'queue', label: '定时计划' },
  { id: 'executions', label: '运行记录' },
]

export const managementTabs = [
  { id: 'services', label: '管理配置' },
  { id: 'hosts', label: '宿主机' },
  { id: 'audit', label: '操作审计' },
]

export const triggerOptions = [
  { value: 'manual', label: '手动触发' },
  { value: 'daily', label: '每日执行' },
  { value: 'weekly', label: '每周执行' },
  { value: 'cron', label: 'Cron 计划' },
]

export const cronPresets = [
  { value: '0 9 * * *', label: '每天 9:00' },
  { value: '0 9,14 * * *', label: '每天 9:00 和 14:00' },
  { value: '*/30 9-18 * * MON-FRI', label: '周一至周五 9:00—18:30，每半小时' },
  { value: '0 9 1 * *', label: '每月 1 日 9:00' },
  { value: '0 18 * * FRI', label: '每周五 18:00' },
]

export const weekdayOptions = [
  { value: 1, label: '一' },
  { value: 2, label: '二' },
  { value: 3, label: '三' },
  { value: 4, label: '四' },
  { value: 5, label: '五' },
  { value: 6, label: '六' },
  { value: 7, label: '日' },
]
