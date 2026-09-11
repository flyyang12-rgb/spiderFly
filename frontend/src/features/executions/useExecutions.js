import { computed, reactive, ref } from 'vue'
import { API, request } from '../../lib/api'

export function createExecutionsState() {
  const EXECUTION_PAGE_SIZE = 10
  const executions = ref([])
  const executionHistory = ref([])
  const executionHistoryTotal = ref(0)
  const executionHistoryPage = ref(1)
  const executionHistoryLoading = ref(false)
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
    if (!me.value) return
    const generation = ++executionHistoryGeneration
    if (!quiet) executionHistoryLoading.value = true
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(EXECUTION_PAGE_SIZE),
    })
    if (executionFilters.task_name.trim()) params.set('task_name', executionFilters.task_name.trim())
    if (executionFilters.status !== 'all') params.set('status', executionFilters.status)
    if (executionFilters.requester.trim()) params.set('requester', executionFilters.requester.trim())
    if (executionFilters.date_from) params.set('date_from', executionFilters.date_from)
    if (executionFilters.date_to) params.set('date_to', executionFilters.date_to)
    try {
      const result = await request('/executions/history?' + params.toString())
      if (generation !== executionHistoryGeneration) return
      const totalPages = Math.max(1, Number(result.total_pages) || 1)
      if (page > totalPages) {
        executionHistoryPage.value = totalPages
        await loadExecutionHistory({ page: totalPages, quiet })
        return
      }
      executionHistory.value = Array.isArray(result.items) ? result.items : []
      executionHistoryTotal.value = Number(result.total) || 0
      executionHistoryPage.value = Number(result.page) || page
    } catch (error) {
      if (generation !== executionHistoryGeneration) return
      if (error.status === 401) handleSessionExpired()
      else if (!quiet) showToast('error', '运行记录暂时没有载入', error.message)
    } finally {
      if (generation === executionHistoryGeneration) executionHistoryLoading.value = false
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
    try {
      await request('/executions/' + item.id + '/cancel', { method: 'POST' })
      showToast('success', item.status === 'pending' ? '已从队列移除' : '已提交停止请求')
      await loadAll({ quiet: true, includeAdmin: false })
    } catch (error) {
      showToast('error', '无法取消', error.message)
    }
  }

  async function forceStopExecution() {
    if (!isAdmin.value || !stoppingExecution.value || stoppingBusy.value) return
    const target = stoppingExecution.value
    stoppingBusy.value = true
    try {
      await request('/executions/' + target.id + '/stop', { method: 'POST' })
      if (detail.value?.id === target.id) detail.value.stop_requested = true
      stoppingExecution.value = null
      showToast('success', '已请求强制停止', '进程退出并完成清理后，队列会继续运行')
      await loadAll({ quiet: true, includeAdmin: false })
    } catch (error) {
      stoppingExecution.value = null
      showToast('error', '无法强制停止', error.message)
      await loadAll({ quiet: true, includeAdmin: false })
    } finally {
      stoppingBusy.value = false
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
    detail.value = { ...item }
    try {
      const result = await request('/executions/' + item.id)
      if (detail.value?.id === item.id) detail.value = result
    } catch (error) {
      if (error.status === 401) handleSessionExpired()
      else if (detail.value?.id === item.id) showToast('error', '无法载入完整日志', error.message)
    }
  }

  function resetArtifactDownload() {
    artifactDownloadGeneration += 1
    artifactDownloadController?.abort()
    artifactDownloadController = null
    Object.assign(artifactDownload, { busy: false, path: '', message: '', error: false })
  }

  async function downloadArtifact(file) {
    if (!detailFinished.value || artifactDownload.busy) return
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
      if (generation !== artifactDownloadGeneration || detail.value?.id !== executionId) return
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
      if (generation !== artifactDownloadGeneration || error.name === 'AbortError') return
      artifactDownload.error = true
      artifactDownload.message =
        error instanceof TypeError ? '暂时无法连接服务器，请检查网络后重试。' : error.message
    } finally {
      if (generation === artifactDownloadGeneration) {
        artifactDownload.busy = false
        artifactDownloadController = null
      }
    }
  }

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
    resetArtifactDownload,
    downloadArtifact,
  }
}
