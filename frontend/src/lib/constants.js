export const baseNavItems = [
  { id: 'overview', label: '工作台', mark: 'W' },
  { id: 'tasks', label: '任务中心', mark: 'T' },
  { id: 'runtime', label: '运行中心', mark: 'R' },
]

export const adminNavItems = [{ id: 'management', label: '管理中心', mark: 'M' }]

export const runtimeTabs = [
  { id: 'active', label: '运行队列' },
  { id: 'queue', label: '任务时间表' },
  { id: 'executions', label: '运行记录' },
]

export const managementTabs = [
  { id: 'hosts', label: '宿主机' },
  { id: 'apps', label: '创建任务' },
  { id: 'ai', label: 'AI 模型' },
  { id: 'users', label: '成员管理' },
  { id: 'settings', label: '系统设置' },
  { id: 'audit', label: '操作审计' },
]

export const triggerOptions = [
  { value: 'manual', label: '手动触发' },
  { value: 'daily', label: '每日执行' },
  { value: 'weekly', label: '每周执行' },
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
