import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { API, request } from '../../lib/api'

export function createExecutionsState() {
  const EXECUTION_PAGE_SIZE = 10
  const executions = ref([])
  const executionHistory = ref([])
  const executionHistoryTotal = ref(0)
  const executionHistoryPage = ref(1)
  const executionHistoryLoading = ref(false)
  const executionHistoryError = ref('')
  const detail = ref(null)
  const artifactDownload = reactive({ busy: false, path: '', message: '', error: false })
  const stoppingExecution = ref(null)
  const stoppingBusy = ref(false)
  const executionFilters = reactive({
    task_name: '',
    status: 'all',
    requester: '',
    date_from: '',
    date_to: '',
  })
  return {
    EXECUTION_PAGE_SIZE,
    executions,
    executionHistory,
    executionHistoryTotal,
    executionHistoryPage,
    executionHistoryLoading,
    executionHistoryError,
    detail,
    artifactDownload,
    stoppingExecution,
    stoppingBusy,
    executionFilters,
  }
}

export function useExecutions({
  EXECUTION_PAGE_SIZE,
  artifactDownload,
  detail,
  executionFilters,
  executionHistory,
  executionHistoryLoading,
  executionHistoryError = ref(''),
  executionHistoryPage,
  executionHistoryTotal,
  executions,
  handleSessionExpired,
  isAdmin,
  loadAll,
  me,
  navigateTo,
  showToast,
  stoppingBusy,
  stoppingExecution,
}) {
  let artifactDownloadController = null

  let artifactDownloadGeneration = 0

  let executionHistoryGeneration = 0
  let detailGeneration = 0
  let sessionGeneration = 0
  let stopGeneration = 0
  let disposed = false
  const controllers = new Set()
  const cancelling = new Set()
  let appliedFilters = { ...executionFilters }

  function sessionToken() { return { generation: sessionGeneration, user: me.value } }
  function currentSession(token) {
    return !disposed && Boolean(token.user) && me.value === token.user && token.generation === sessionGeneration
  }
  function ready() { return !disposed && Boolean(me.value) && !me.value.must_change_password }
  async function api(path, options = {}) {
    const controller = new AbortController()
    controllers.add(controller)
    try { return await request(path, { ...options, signal: controller.signal }) }
    finally { controllers.delete(controller) }
  }
  function actionFailed(error, title) {
    if (error.name === 'AbortError') return
    if (error.status === 401) handleSessionExpired()
    else showToast('error', title, error.message)
  }

  const activeExecutions = computed(() =>
    executions.value.filter((item) => ['pending', 'running'].includes(item.status)),
  )

  const runningExecution = computed(() => executions.value.find((item) => item.status === 'running') || null)

  const queuedExecutions = computed(() =>
    executions.value
      .filter((item) => item.status === 'pending')
      .sort((a, b) => (a.queue_position ?? 999999) - (b.queue_position ?? 999999)),
  )

  const completedExecutions = computed(() =>
    executions.value.filter((item) => !['pending', 'running'].includes(item.status)),
  )

  const orderedActiveExecutions = computed(() => [
    ...(runningExecution.value ? [runningExecution.value] : []),
    ...queuedExecutions.value,
  ])

  const executionHistoryTotalPages = computed(() =>
    Math.max(1, Math.ceil(executionHistoryTotal.value / EXECUTION_PAGE_SIZE)),
  )

  const executionHistoryStart = computed(() =>
    executionHistoryTotal.value ? (executionHistoryPage.value - 1) * EXECUTION_PAGE_SIZE + 1 : 0,
  )

  const executionHistoryEnd = computed(() =>
    Math.min(executionHistoryPage.value * EXECUTION_PAGE_SIZE, executionHistoryTotal.value),
  )

  const executionFiltersActive = computed(() =>
    Boolean(
      executionFilters.task_name.trim() ||
      executionFilters.status !== 'all' ||
      executionFilters.requester.trim() ||
      executionFilters.date_from ||
      executionFilters.date_to,
    ),
  )

  const detailFinished = computed(() =>
    ['success', 'failed', 'timeout', 'cancelled'].includes(detail.value?.status),
  )

  const artifactFiles = computed(() =>
    Array.isArray(detail.value?.artifacts?.files) ? detail.value.artifacts.files : [],
  )

  async function loadExecutionHistory({ page = executionHistoryPage.value, quiet = false } = {}) {
    if (disposed || !me.value) return
    if (quiet && executionHistoryLoading.value) return
    const token = sessionToken()
    const generation = ++executionHistoryGeneration
    if (!quiet) {
      executionHistoryLoading.value = true
      appliedFilters = { ...executionFilters }
    }
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(EXECUTION_PAGE_SIZE),
    })
    if (appliedFilters.task_name.trim()) params.set('task_name', appliedFilters.task_name.trim())
    if (appliedFilters.status !== 'all') params.set('status', appliedFilters.status)
    if (appliedFilters.requester.trim()) params.set('requester', appliedFilters.requester.trim())
    if (appliedFilters.date_from) params.set('date_from', appliedFilters.date_from)
    if (appliedFilters.date_to) params.set('date_to', appliedFilters.date_to)
    try {
      const result = await api('/executions/history?' + params.toString())
      if (!currentSession(token) || generation !== executionHistoryGeneration) return
      if (!Array.isArray(result?.items)) throw new Error('运行记录返回格式不完整，请重试读取')
      const totalPages = Math.max(1, Number(result.total_pages) || 1)
      if (page > totalPages) {
        executionHistoryPage.value = totalPages
        await loadExecutionHistory({ page: totalPages, quiet })
        return
      }
      executionHistory.value = result.items
      executionHistoryTotal.value = Number(result.total) || 0
      executionHistoryPage.value = Number(result.page) || page
      executionHistoryError.value = ''
    } catch (error) {
      if (!currentSession(token) || generation !== executionHistoryGeneration || error.name === 'AbortError') return
      executionHistoryError.value = error.message || '运行记录暂时没有载入'
      if (error.status === 401) handleSessionExpired()
    } finally {
      if (currentSession(token) && generation === executionHistoryGeneration) executionHistoryLoading.value = false
    }
  }

  async function applyExecutionFilters() {
    if (
      executionFilters.date_from &&
      executionFilters.date_to &&
      executionFilters.date_from > executionFilters.date_to
    ) {
      showToast('error', '开始日期不能晚于结束日期')
      return
    }
    executionHistoryPage.value = 1
    await loadExecutionHistory({ page: 1 })
  }

  async function resetExecutionFilters() {
    Object.assign(executionFilters, {
      task_name: '',
      status: 'all',
      requester: '',
      date_from: '',
      date_to: '',
    })
    executionHistoryPage.value = 1
    await loadExecutionHistory({ page: 1 })
  }

  async function goToExecutionPage(page) {
    const target = Math.min(executionHistoryTotalPages.value, Math.max(1, page))
    if (target === executionHistoryPage.value || executionHistoryLoading.value) return
    await loadExecutionHistory({ page: target })
  }

  async function cancelExecution(item) {
    if (!ready() || cancelling.has(item.id) || (item.status !== 'pending' && !isAdmin.value)) return
    const token = sessionToken()
    cancelling.add(item.id)
    try {
      await api('/executions/' + item.id + '/cancel', { method: 'POST' })
      if (!currentSession(token)) return
      if (item.trigger_source === 'workflow') {
        showToast('success', '已提交所属流程的停止请求', '后续步骤不再执行，当前步骤完成收尾后结束本轮')
      } else showToast('success', item.status === 'pending' ? '已从队列移除' : '已提交停止请求')
      await loadAll({ quiet: true, includeAdmin: false })
    } catch (error) {
      if (currentSession(token)) actionFailed(error, '无法取消')
    } finally {
      if (currentSession(token)) cancelling.delete(item.id)
    }
  }

  async function forceStopExecution() {
    if (!ready() || !isAdmin.value || !stoppingExecution.value || stoppingBusy.value) return
    const target = stoppingExecution.value
    const token = sessionToken()
    const generation = ++stopGeneration
    stoppingBusy.value = true
    try {
      await api('/executions/' + target.id + '/stop', { method: 'POST' })
      if (!currentSession(token) || generation !== stopGeneration) return
      if (detail.value?.id === target.id) detail.value.stop_requested = true
      if (stoppingExecution.value === target) stoppingExecution.value = null
      showToast('success', target.trigger_source === 'workflow' ? '已请求强制停止所属流程' : '已请求强制停止',
        target.trigger_source === 'workflow' ? '后续步骤不再执行，当前步骤完成收尾后结束本轮' : '进程退出并完成清理后，队列会继续运行')
      await loadAll({ quiet: true, includeAdmin: false })
    } catch (error) {
      if (!currentSession(token) || generation !== stopGeneration || error.name === 'AbortError') return
      if (stoppingExecution.value === target) stoppingExecution.value = null
      actionFailed(error, '无法强制停止')
      if (!currentSession(token)) return
      await loadAll({ quiet: true, includeAdmin: false })
    } finally {
      if (currentSession(token) && generation === stopGeneration) stoppingBusy.value = false
    }
  }

  function taskIsActive(task) {
    return activeExecutions.value.some((item) => Number(item.task_id) === Number(task.id))
  }

  function viewTaskExecution(task) {
    const execution = orderedActiveExecutions.value.find((item) => Number(item.task_id) === Number(task.id))
    if (execution) return openExecution(execution)
    showToast('info', '本次运行已结束', '请到运行记录查看结果')
    navigateTo('runtime', 'executions')
  }

  async function openExecution(item) {
    if (disposed || !me.value || !item?.id) return
    resetArtifactDownload()
    detail.value = { ...item }
    return refreshExecutionDetail()
  }

  async function refreshExecutionDetail({ quiet = false } = {}) {
    if (disposed || !me.value || !detail.value?.id) return
    const id = detail.value.id
    const token = sessionToken()
    const generation = ++detailGeneration
    try {
      const result = await api('/executions/' + id)
      if (currentSession(token) && generation === detailGeneration && detail.value?.id === id) detail.value = result
    } catch (error) {
      if (!currentSession(token) || generation !== detailGeneration || detail.value?.id !== id || error.name === 'AbortError') return
      if (error.status === 401) handleSessionExpired()
      else if (!quiet) showToast('error', '无法载入完整日志', error.message)
    }
  }

  function resetArtifactDownload() {
    artifactDownloadGeneration += 1
    artifactDownloadController?.abort()
    artifactDownloadController = null
    Object.assign(artifactDownload, { busy: false, path: '', message: '', error: false })
  }

  async function downloadArtifact(file) {
    if (disposed || !me.value || !detailFinished.value || artifactDownload.busy) return
    const token = sessionToken()
    const executionId = detail.value.id
    const generation = artifactDownloadGeneration
    const controller = new AbortController()
    artifactDownloadController = controller
    Object.assign(artifactDownload, { busy: true, path: file.path, message: '', error: false })
    const url =
      API + '/executions/' + executionId + '/artifacts/download?path=' + encodeURIComponent(file.path)
    try {
      const response = await fetch(url, {
        method: 'HEAD',
        credentials: 'include',
        cache: 'no-store',
        signal: controller.signal,
      })
      if (!currentSession(token) || generation !== artifactDownloadGeneration || detail.value?.id !== executionId) return
      if (response.status === 401) {
        handleSessionExpired()
        return
      }
      if (!response.ok) {
        throw new Error(
          {
            403: '你没有下载这个文件的权限。',
            404: '文件已不在原位置，请刷新运行记录后查看。',
            409: '任务还未结束，请在运行结束后下载。',
          }[response.status] || '文件暂时无法下载，请稍后重试。',
        )
      }
      // 交给浏览器直接下载，避免把整个文件读入页面内存。
      const link = document.createElement('a')
      link.href = url
      link.download = file.name || ''
      document.body.appendChild(link)
      link.click()
      link.remove()
      artifactDownload.message = '下载请求已交给浏览器，请在浏览器下载列表查看。'
    } catch (error) {
      if (!currentSession(token) || generation !== artifactDownloadGeneration || detail.value?.id !== executionId || error.name === 'AbortError') return
      artifactDownload.error = true
      artifactDownload.message =
        error instanceof TypeError ? '暂时无法连接服务器，请检查网络后重试。' : error.message
    } finally {
      if (currentSession(token) && generation === artifactDownloadGeneration) {
        artifactDownload.busy = false
        artifactDownloadController = null
      }
    }
  }

  function suspendExecutionHistory() {
    executionHistoryGeneration += 1
    executionHistoryLoading.value = false
  }

  function resetExecutionSession() {
    sessionGeneration += 1
    detailGeneration += 1
    stopGeneration += 1
    suspendExecutionHistory()
    controllers.forEach((controller) => controller.abort())
    resetArtifactDownload()
    cancelling.clear()
    executions.value = []; executionHistory.value = []; detail.value = null
    executionHistoryTotal.value = 0; executionHistoryPage.value = 1; executionHistoryError.value = ''
    stoppingExecution.value = null; stoppingBusy.value = false
    Object.assign(executionFilters, { task_name: '', status: 'all', requester: '', date_from: '', date_to: '' })
    appliedFilters = { ...executionFilters }
  }

  watch(me, (user, previous) => { if (user !== previous) resetExecutionSession() }, { flush: 'sync' })
  watch(() => detail.value?.id, () => { detailGeneration += 1; resetArtifactDownload() }, { flush: 'sync' })
  onBeforeUnmount(() => { disposed = true; resetExecutionSession() })

  return {
    activeExecutions,
    runningExecution,
    queuedExecutions,
    completedExecutions,
    orderedActiveExecutions,
    executionHistoryTotalPages,
    executionHistoryStart,
    executionHistoryEnd,
    executionFiltersActive,
    detailFinished,
    artifactFiles,
    loadExecutionHistory,
    applyExecutionFilters,
    resetExecutionFilters,
    goToExecutionPage,
    cancelExecution,
    forceStopExecution,
    taskIsActive,
    viewTaskExecution,
    openExecution,
    refreshExecutionDetail,
    suspendExecutionHistory,
    resetExecutionSession,
    resetArtifactDownload,
    downloadArtifact,
  }
}
