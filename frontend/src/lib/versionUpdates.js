export function updateContext(state, update) {
  if (!update) return null
  const version = state.versions.find(v => v.id === update.version_id)
  const base = state.versions.find(v => v.id === version?.base_version_id)
  const repaired = version?.origin === 'repair' && Boolean(base)
  const target = version ? `v${version.sequence}` : `v${update.sequence}`
  const prefix = repaired ? `${'v' + base.sequence} 验证未通过，已生成修复版 ${target}` : `${target} 更新`
  const states = {
    candidate: '已保存为未运行候选，等待管理员确认',
    pending: '等待验证，当前版本保持不变', testing: '正在验证，当前版本保持不变',
    repairing: '验证未通过，正在尝试自动修复，当前版本保持不变',
    ready: '验证通过，请确认是否使用', activated: '已确认启用',
    failed: '更新失败，未启用此候选版本', cancelled: '本次更新已取消',
    interrupted: '本次更新已中断', conflict: '需要确认需求差异',
  }
  const current = state.versions.find(v => v.id === state.active_version_id)
  return {
    title: `${prefix}；${states[update.status] || update.status}`,
    current: current ? `当前使用 v${current.sequence}` : '',
    repaired,
    // Keep the original validation output separate from the repaired trial.
    originalLog: (update.log || '').split('\n修复试跑：')[0].trim(),
  }
}

export function repairFor(state, version) {
  if (version.approved) return null
  return state.versions.find(v => v.origin === 'repair' && v.base_version_id === version.id)
}
