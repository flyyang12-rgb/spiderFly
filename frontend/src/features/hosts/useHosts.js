import { onBeforeUnmount, onMounted, ref } from 'vue'
import { request } from '../../lib/api'

export const hostStateLabels = {
  pending: '待批准', non_dispatch: '非调度', idle: '调度空闲', running: '运行中',
  stopping: '正在停止', offline: '离线', uncertain: '状态待确认', rejected: '已拒绝', revoked: '已撤销',
}

export const runStatusLabels = {
  queued: '排队中', preparing: '准备环境', running: '运行中', stopping: '正在停止',
  uncertain: '状态待确认', succeeded: '成功', failed: '失败', cancelled: '已取消', timed_out: '超时',
}

export const activeRunStates = new Set(['queued', 'preparing', 'running', 'stopping', 'uncertain'])

export function useHosts({ onSessionExpired }) {
  const hosts = ref([])
  const runs = ref([])
  const loading = ref(true)
  const refreshing = ref(false)
  const loadError = ref('')
  const actionError = ref('')
  const notice = ref('')
  const busy = ref('')
  const enrollment = ref(null)
  const selectedRunId = ref(null)
  const runDetail = ref(null)
  const detailLoading = ref(false)
  const detailError = ref('')
  let disposed = false
  let pollTimer = 0
  let refreshSequence = 0
  let detailSequence = 0

  function fail(error, target) {
    if (disposed) return
    target.value = error.message || '连接失败，请稍后重试'
    if (error.status === 401) onSessionExpired()
  }

  async function loadRunDetail(id, quiet = false) {
    const sequence = ++detailSequence
    if (!quiet) {
      detailLoading.value = true
      detailError.value = ''
    }
    try {
      const result = await request(`/remote-runs/${id}`)
      if (!disposed && selectedRunId.value === id && sequence === detailSequence) {
        runDetail.value = result
        detailError.value = ''
      }
    } catch (error) {
      if (selectedRunId.value === id && sequence === detailSequence) fail(error, detailError)
    } finally {
      if (sequence === detailSequence) detailLoading.value = false
    }
  }

  async function openRun(id) {
    selectedRunId.value = id
    runDetail.value = null
    await loadRunDetail(id)
  }

  function closeRun() {
    selectedRunId.value = null
    runDetail.value = null
    detailError.value = ''
    detailLoading.value = false
    detailSequence += 1
  }

  function scheduleRefresh() {
    window.clearTimeout(pollTimer)
    if (disposed) return
    pollTimer = window.setTimeout(() => {
      if (document.hidden) scheduleRefresh()
      else refresh(true)
    }, runs.value.some((run) => activeRunStates.has(run.status)) ? 2500 : 5000)
  }

  async function refresh(quiet = false) {
    const sequence = ++refreshSequence
    if (!quiet) refreshing.value = true
    try {
      const [hostResult, runResult] = await Promise.all([
        request('/hosts'), request('/remote-runs'),
      ])
      if (disposed || sequence !== refreshSequence) return
      hosts.value = hostResult
      runs.value = runResult
      loadError.value = ''
      if (selectedRunId.value != null) await loadRunDetail(selectedRunId.value, true)
    } catch (error) {
      if (sequence === refreshSequence) fail(error, loadError)
    } finally {
      if (sequence === refreshSequence) {
        loading.value = false
        refreshing.value = false
        scheduleRefresh()
      }
    }
  }

  async function perform(key, operation, successMessage) {
    if (busy.value) return null
    busy.value = key
    actionError.value = ''
    notice.value = ''
    try {
      const result = await operation()
      if (disposed) return result
      notice.value = typeof successMessage === 'function' ? successMessage(result) : successMessage
      await refresh(true)
      return result
    } catch (error) {
      fail(error, actionError)
      return null
    } finally {
      busy.value = ''
    }
  }

  async function createEnrollment() {
    const result = await perform('enrollment', () => request('/hosts/enrollment-codes', { method: 'POST', body: '{}' }), '接入码已生成，请在有效期内用于一台新电脑。')
    if (result && !disposed) enrollment.value = result
  }

  async function hostAction(host, action) {
    const verbs = { approve: '批准', reject: '拒绝', revoke: '撤销' }
    return perform(`host:${host.id}`, () => request(`/hosts/${host.id}/${action}`, { method: 'POST', body: '{}' }), `已${verbs[action]}宿主机「${host.name}」`)
  }

  async function setMode(host) {
    const enabled = !host.dispatch_enabled
    return perform(`host:${host.id}`, () => request(`/hosts/${host.id}/mode`, {
      method: 'PATCH', body: JSON.stringify({ dispatch_enabled: enabled }),
    }), enabled ? `已允许「${host.name}」接收调度；本机也需开启调度模式。` : `已关闭「${host.name}」的主控调度。`)
  }

  async function stopRun(run) {
    return perform(`stop:${run.id}`, () => request(`/remote-runs/${run.id}/stop`, { method: 'POST', body: '{}' }),
      (result) => result.status === 'cancelled' ? `运行 #${run.id} 已取消。` : `已请求停止运行 #${run.id}，等待宿主机确认。`)
  }

  async function activateCandidate(maintenance) {
    return perform(`candidate:${maintenance.update_id}`, () => request(`/task-versions/updates/${maintenance.update_id}/activate`, {
      method: 'POST', body: '{}',
    }), (result) => `已确认使用 v${result.version}，远程重跑 #${result.remote_run_id} 已提交到原宿主机。`)
  }

  async function dismissCandidate(maintenance) {
    return perform(`candidate:${maintenance.update_id}`, () => request(`/task-versions/updates/${maintenance.update_id}/stop`, {
      method: 'POST', body: '{}',
    }), '已保留候选历史，本次不启用也不重跑。')
  }

  onMounted(() => refresh())
  onBeforeUnmount(() => {
    disposed = true
    window.clearTimeout(pollTimer)
    refreshSequence += 1
    detailSequence += 1
    enrollment.value = null
  })

  return {
    hosts, runs, loading, refreshing, loadError, actionError, notice, busy, enrollment,
    selectedRunId, runDetail, detailLoading, detailError,
    refresh, createEnrollment, hostAction, setMode, stopRun,
    activateCandidate, dismissCandidate,
    openRun, closeRun, loadRunDetail,
  }
}
