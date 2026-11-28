import { onBeforeUnmount, watch } from 'vue'
import { request } from '../../lib/api'
import { formatDuration } from '../../lib/format'

const outcomes = Object.assign(Object.create(null), {
  success: ['success', '运行成功'], succeeded: ['success', '运行成功'],
  failed: ['error', '运行失败'], timeout: ['error', '运行超时'], timed_out: ['error', '运行超时'],
  cancelled: ['info', '运行已停止'],
})
const maxAge = 24 * 60 * 60 * 1000

// Follow the receipt, including runs that finish between two workspace refreshes.
export function useRunFeedback({ me, showToast, openExecution, openRemoteRun, navigateTo, handleSessionExpired }) {
  let records = []
  let generation = 0
  let owner = null
  let timer = 0
  let disposed = false
  const announced = new Set()
  const controllers = new Set()
  const key = () => `spiderfly.run-feedback.${owner}`
  const identity = (run) => `${run.kind}:${run.id}`

  function persist() {
    if (!owner) return
    try {
      if (records.length) sessionStorage.setItem(key(), JSON.stringify(records))
      else sessionStorage.removeItem(key())
    } catch { /* Tracking still works when browser storage is unavailable. */ }
  }

  function announce(run) {
    const outcome = outcomes[run.status]
    if (!outcome || announced.has(identity(run))) return
    announced.add(identity(run))
    const session = generation
    const acknowledge = () => {
      if (session !== generation) return
      records = records.filter((item) => identity(item) !== identity(run))
      persist()
    }
    showToast(outcome[0], outcome[1], `${run.name} · #${run.id}${run.duration_ms == null ? '' : ' · ' + formatDuration(run.duration_ms)}`, {
      sticky: true,
      actionLabel: '查看日志',
      onDismiss: acknowledge,
      action: () => {
        if (session !== generation || !owner) return
        if (run.kind === 'remote') {
          openRemoteRun(run.id)
        } else {
          navigateTo('runtime', 'executions')
          openExecution({ id: run.id, task_name: run.name })
        }
      },
    })
  }

  function schedule(delay = 2000) {
    window.clearTimeout(timer)
    if (!disposed && owner && records.some((run) => !outcomes[run.status])) {
      timer = window.setTimeout(poll, delay)
    }
  }

  async function poll() {
    if (controllers.size) { schedule(); return }
    const session = generation
    await Promise.all(records.filter((run) => !outcomes[run.status]).map(async (run) => {
      const controller = new AbortController()
      controllers.add(controller)
      const timeout = window.setTimeout(() => controller.abort(), 10000)
      try {
        const path = run.kind === 'remote' ? '/remote-runs/' : '/executions/'
        const result = await request(path + run.id, { signal: controller.signal })
        if (disposed || session !== generation || !records.includes(run)) return
        if (outcomes[result?.status]) {
          run.status = result.status
          const duration = run.kind === 'remote'
            ? Date.parse(result.finished_at) - Date.parse(result.started_at)
            : result.duration_ms
          run.duration_ms = duration != null && Number.isFinite(duration) && duration >= 0 ? duration : null
          persist()
          announce(run)
        }
      } catch (error) {
        if (disposed || session !== generation) return
        if (error.status === 401) handleSessionExpired()
        else if ([403, 404].includes(error.status)) {
          records = records.filter((item) => item !== run)
          persist()
        }
        // A failed request or an uncertain Agent is never a completed run.
      } finally {
        window.clearTimeout(timeout)
        controllers.delete(controller)
      }
    }))
    if (session === generation) schedule()
  }

  function reset() {
    generation++
    window.clearTimeout(timer)
    for (const controller of controllers) controller.abort()
    controllers.clear()
    records = []
    announced.clear()
  }

  watch(() => me.value?.id ?? null, (id) => {
    // Explicit logout/account switch removes the previous user's browser receipts.
    if (owner) { try { sessionStorage.removeItem(key()) } catch {} }
    reset()
    owner = id
    if (!owner) return
    try {
      const stored = JSON.parse(sessionStorage.getItem(key()) || '[]')
      if (Array.isArray(stored)) records = stored.filter((run) =>
        ['local', 'remote'].includes(run?.kind) && Number.isSafeInteger(run.id) && run.id > 0 &&
        typeof run.name === 'string' && Number.isFinite(run.created) &&
        Date.now() - run.created >= 0 && Date.now() - run.created < maxAge,
      ).slice(-20)
    } catch {}
    persist()
    records.forEach(announce)
    schedule(0)
  }, { immediate: true, flush: 'sync' })

  function trackRun(kind, id, name) {
    id = Number(id)
    if (!owner || !['local', 'remote'].includes(kind) || !Number.isSafeInteger(id) || id <= 0) return
    if (records.some((run) => run.kind === kind && run.id === id)) return
    records.push({ kind, id, name: String(name || '任务'), created: Date.now() })
    records = records.slice(-20)
    persist()
    schedule()
  }

  onBeforeUnmount(() => { disposed = true; reset() })
  return { trackRun }
}
