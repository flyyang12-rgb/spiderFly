export function maintenanceResult(job) {
  if (job?.status !== 'activated') return ''
  if (job.rerun_reason) return job.rerun_reason
  if (!job.rerun_execution_id) return '试跑通过'
  return ({ pending: '等待自动重跑', running: '正在自动重跑', success: '自动重跑成功', failed: '自动重跑失败', timeout: '自动重跑超时', cancelled: '自动重跑已取消' })[job.rerun_status] || '重跑记录已清理'
}

// Keep the audit trail intact and distinguish trial validation from the real run.
export function maintenanceSummary(job) {
  const note = (job?.note || '').trim()
  if (job?.status !== 'activated') return note || '处理中…'

  const change = note
    .replace(/(?:；|^)已启用\s*(?:修复版本|V\d+)(?:，下次运行使用新代码)?[。.]?$/, '')
    .replace(/(?:；|^)(?:原错误已复现，修复后运行通过|原验收条件通过，实际\s*\d+\s*条)[。.]?$/, '')
    .replace(/[，,；;]\s*保持原有输出意图[。.]?$/, '')
    .replace(/[。；;\s]+$/, '')
    .replace(/`([^`\n]+)`/g, '“$1”')
  const result = maintenanceResult(job)
  return change ? `${change}；${result}。` : `${result}。`
}
