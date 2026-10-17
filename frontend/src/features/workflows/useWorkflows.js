import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, reactive, ref, watch } from 'vue'
import { request } from '../../lib/api.js'
import { emptyPlan, readPlan, planBody, validatePlan, creationRequestId } from '../../lib/plans'

export const workflowStatusLabels = {
  waiting: '等待中', pending: '排队中', running: '运行中', stopping: '停止中',
  success: '成功', failed: '失败', timeout: '超时', cancelled: '已取消', skipped: '已跳过',
}
export const activeWorkflowStates = new Set(['waiting', 'running', 'stopping'])
export function workflowStatusLabel(status) { return workflowStatusLabels[status] || status || '未运行' }
export function workflowReasonPreview(text) {
  const lastLine = String(text || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean).at(-1) || ''
  const characters = Array.from(lastLine)
  return characters.length > 160 ? characters.slice(0, 160).join('') + '…' : lastLine
}

function requestId() {
  return 'workflow-manual-' + [...crypto.getRandomValues(new Uint8Array(16))]
    .map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

export function useWorkflows({ isAdmin, me, onSessionExpired, workflowPendingStarts, mode = 'all' }) {
  const includeHistory = mode !== 'definitions'
  const workflows = ref([])
  const runs = ref([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = 10
  const loading = ref(true)
  const definitionsLoaded = ref(false)
  const refreshing = ref(false)
  const historyLoading = ref(false)
  const loadError = ref('')
  const historyError = ref('')
  const actionError = ref('')
  const notice = ref('')
  const busy = ref('')
  const showArchived = ref(false)
  const filters = reactive({ workflow_id: '', status: 'all', date_from: '', date_to: '' })
  let appliedFilters = { ...filters }
  const editor = ref(null)
  const form = reactive({ name: '', description: '', task_ids: [], ...emptyPlan() })
  let originalPlan = ''
  let pendingCreation = null
  const formError = ref('')
  const formUncertain = ref(false)
  const candidates = ref([])
  const candidatesLoading = ref(false)
  const candidatesError = ref('')
  const selectedTask = ref('')
  const selectedRunId = ref(null)
  const runDetail = ref(null)
  const detailLoading = ref(false)
  const detailError = ref('')
  const confirmation = ref(null)
  const pendingStarts = workflowPendingStarts || reactive(new Map())
  const visibleWorkflows = computed(() => workflows.value.filter((item) => showArchived.value || !item.archived))
  const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
  const selectedSteps = computed(() => form.task_ids.map((id) => {
    const current = candidates.value.find((task) => Number(task.id) === Number(id))
    const existing = editor.value?.steps?.find((step) => Number(step.task_id) === Number(id))
    return current || { id, name: existing?.task_name || `任务 #${id}`, eligible: false, unavailable_reason: '任务已删除或不可用' }
  }))
  let disposed = false
  let active = true
  let pollTimer = 0
  let listSequence = 0
  let historySequence = 0
  let detailSequence = 0
  let candidateSequence = 0
  let sessionSequence = 0
  const controllers = new Set()

  function live() { return !disposed && active && Boolean(me.value) }

  async function api(path, options = {}) {
    const controller = new AbortController()
    controllers.add(controller)
    try { return await request(path, { ...options, signal: controller.signal }) }
    finally { controllers.delete(controller) }
  }

  function fail(error, target) {
    if (!live() || error.name === 'AbortError') return
    target.value = error.message || '连接失败，请稍后重试'
    if (error.status === 401) onSessionExpired()
  }

  async function loadHistory({ targetPage = page.value, quiet = false } = {}) {
    if (!live()) return
    // A poll must not replace a user's in-flight page or filter request.
    if (quiet && historyLoading.value) return
    const sequence = ++historySequence
    const session = sessionSequence
    const user = me.value
    if (!quiet) historyLoading.value = true
    const params = new URLSearchParams({ page: String(targetPage), page_size: String(pageSize) })
    for (const [key, value] of Object.entries(appliedFilters)) {
      if (value && value !== 'all') params.set(key, value)
    }
    try {
      const result = await api('/workflow-runs?' + params)
      if (!live() || user !== me.value || session !== sessionSequence || sequence !== historySequence) return
      if (!Array.isArray(result?.items)) throw new Error('流程记录返回格式不完整，请重试读取')
      const lastPage = Math.max(1, Math.ceil((Number(result.total) || 0) / pageSize))
      if (targetPage > lastPage) return await loadHistory({ targetPage: lastPage, quiet })
      runs.value = result.items
      total.value = Number(result.total) || 0
      page.value = Number(result.page) || targetPage
      historyError.value = ''
    } catch (error) { if (user === me.value && session === sessionSequence && sequence === historySequence) fail(error, historyError) }
    finally { if (user === me.value && session === sessionSequence && sequence === historySequence) historyLoading.value = false }
  }

  async function applyFilters() {
    if (filters.date_from && filters.date_to && filters.date_from > filters.date_to) {
      historyError.value = '开始日期不能晚于结束日期'
      return
    }
    appliedFilters = { ...filters }
    await loadHistory({ targetPage: 1 })
  }

  async function resetFilters() {
    Object.assign(filters, { workflow_id: '', status: 'all', date_from: '', date_to: '' })
    await applyFilters()
  }

  async function loadRunDetail(id, quiet = false) {
    if (!live()) return
    if (quiet && detailLoading.value) return
    const sequence = ++detailSequence
    const session = sessionSequence
    const user = me.value
    if (!quiet) { detailLoading.value = true; detailError.value = '' }
    try {
      const result = await api(`/workflow-runs/${id}`)
      if (!live() || user !== me.value || session !== sessionSequence || selectedRunId.value !== id || sequence !== detailSequence) return
      runDetail.value = result
      detailError.value = ''
    } catch (error) { if (user === me.value && session === sessionSequence && selectedRunId.value === id && sequence === detailSequence) fail(error, detailError) }
    finally { if (user === me.value && session === sessionSequence && sequence === detailSequence) detailLoading.value = false }
  }

  async function openRun(id) {
    selectedRunId.value = Number(id)
    runDetail.value = null
    await loadRunDetail(Number(id))
  }

  function closeRun() {
    selectedRunId.value = null
    runDetail.value = null
    detailError.value = ''
    detailLoading.value = false
    detailSequence += 1
  }

  async function refresh(quiet = false) {
    if (!live()) return
    const sequence = ++listSequence
    const session = sessionSequence
    const user = me.value
    if (!quiet) refreshing.value = true
    // History and its definition filter have independent failures. A failed
    // definition request must not prevent a valid history query from loading.
    const historyRequest = includeHistory ? loadHistory({ quiet }) : null
    try {
      const result = await api('/workflows?include_archived=true')
      if (!live() || user !== me.value || session !== sessionSequence || sequence !== listSequence) return
      if (!Array.isArray(result)) throw new Error('流程列表返回格式不完整，请重试读取')
      workflows.value = result
      definitionsLoaded.value = true
      loadError.value = ''
    } catch (error) {
      if (user === me.value && session === sessionSequence && sequence === listSequence) {
        definitionsLoaded.value = false
        fail(error, loadError)
      }
    }
    finally {
      await Promise.all([historyRequest,
        live() && user === me.value && session === sessionSequence && selectedRunId.value != null
          ? loadRunDetail(selectedRunId.value, true) : null,
      ])
      if (user === me.value && session === sessionSequence && sequence === listSequence) {
        loading.value = false
        refreshing.value = false
        scheduleRefresh()
      }
    }
  }

  function scheduleRefresh() {
    window.clearTimeout(pollTimer)
    if (!live()) return
    const active = workflows.value.some((flow) => flow.active_run_id) || activeWorkflowStates.has(runDetail.value?.status)
    pollTimer = window.setTimeout(() => document.hidden ? scheduleRefresh() : refresh(true), active ? 1500 : 5000)
  }

  async function loadCandidates() {
    if (!live() || !isAdmin.value || !editor.value) return
    const sequence = ++candidateSequence
    const session = sessionSequence
    const user = me.value
    candidatesLoading.value = true
    try {
      const result = await api('/workflows/tasks')
      if (!live() || user !== me.value || session !== sessionSequence || sequence !== candidateSequence || !editor.value) return
      candidates.value = Array.isArray(result) ? result.map((task) => ({
        ...task, eligible: Boolean(task.eligible ?? task.available),
        unavailable_reason: task.unavailable_reason ?? task.reason ?? '',
      })) : []
      candidatesError.value = ''
    } catch (error) { if (user === me.value && session === sessionSequence && sequence === candidateSequence) fail(error, candidatesError) }
    finally { if (user === me.value && session === sessionSequence && sequence === candidateSequence) candidatesLoading.value = false }
  }

  function openEditor(flow = null) {
    if (!live() || !isAdmin.value || busy.value) return
    editor.value = flow ? { ...flow, steps: [...(flow.steps || [])] } : { id: null, steps: [] }
    Object.assign(form, {
      name: flow?.name || '', description: flow?.description || '',
      task_ids: (flow?.steps || []).map((step) => Number(step.task_id)),
      ...readPlan(flow, true),
    })
    originalPlan = JSON.stringify(planBody(form, true))
    pendingCreation = null
    formUncertain.value = false
    formError.value = ''
    selectedTask.value = ''
    candidates.value = []
    candidatesError.value = ''
    loadCandidates()
  }

  function closeEditor() {
    if (busy.value === 'save') return
    editor.value = null
    candidateSequence += 1
    candidates.value = []
    candidatesLoading.value = false
    formError.value = ''
  }

  function addStep() {
    const id = Number(selectedTask.value)
    const task = candidates.value.find((candidate) => Number(candidate.id) === id)
    if (!task?.eligible || form.task_ids.includes(id)) return
    form.task_ids.push(id)
    selectedTask.value = ''
    formError.value = ''
  }

  function moveStep(index, offset) {
    const target = index + offset
    if (target < 0 || target >= form.task_ids.length) return
    const updated = [...form.task_ids]
    ;[updated[index], updated[target]] = [updated[target], updated[index]]
    form.task_ids = updated
  }

  async function saveWorkflow({ retryUncertain = false } = {}) {
    if (!live() || !isAdmin.value || !editor.value || busy.value) return
    if (formUncertain.value && !retryUncertain) return
    if (!form.name.trim()) { formError.value = '请填写流程名称'; return }
    if (!form.task_ids.length) { formError.value = '请至少添加一个任务'; return }
    const planError = validatePlan(form)
    if (planError) { formError.value = planError; return }
    const editing = editor.value
    const payload = { name: form.name.trim(), description: form.description.trim(), task_ids: [...form.task_ids] }
    const schedule = planBody(form, true)
    if (!editing.id || JSON.stringify(schedule) !== originalPlan) payload.schedule = schedule
    if (editing.id) payload.revision = editing.revision
    else { pendingCreation ||= { ...payload, request_id: creationRequestId() }; Object.assign(payload, pendingCreation) }
    const session = sessionSequence
    busy.value = 'save'
    formError.value = ''
    try {
      const result = await api(editing.id ? `/workflows/${editing.id}` : '/workflows', {
        method: editing.id ? 'PATCH' : 'POST', body: JSON.stringify(payload),
      })
      if (!live() || session !== sessionSequence) return
      editor.value = null
      notice.value = '流程已保存。'
      await refresh(true)
      if (live() && session === sessionSequence) return result
    } catch (error) {
      if (session === sessionSequence) {
        formUncertain.value = !editing.id && (!error.status || error.status >= 500)
        if (!formUncertain.value) pendingCreation = null
        fail(error, formError)
      }
    }
    finally { if (session === sessionSequence) busy.value = '' }
  }

  async function resolveCreation() {
    if (!live() || !isAdmin.value || busy.value || !pendingCreation || !formUncertain.value) return
    const session = sessionSequence
    busy.value = 'save'
    try {
      await api('/creation-requests/workflow/' + pendingCreation.request_id)
      if (!live() || session !== sessionSequence) return
      editor.value = null; formUncertain.value = false; pendingCreation = null
      notice.value = '流程已保存。'
      await refresh(true)
    } catch (error) { if (session === sessionSequence) fail(error, formError) }
    finally { if (session === sessionSequence) busy.value = '' }
  }

  async function startWorkflow(flow, rerunOf = null) {
    if (!live() || !isAdmin.value || busy.value || flow.archived) return
    const existing = pendingStarts.get(flow.id)
    const pending = existing || { request_id: requestId(), rerun_of: rerunOf }
    pendingStarts.set(flow.id, pending)
    busy.value = `start:${flow.id}`
    actionError.value = ''
    notice.value = ''
    const session = sessionSequence
    try {
      const result = await api(`/workflows/${flow.id}/runs`, { method: 'POST', body: JSON.stringify(pending) })
      if (!live() || session !== sessionSequence) return
      pendingStarts.delete(flow.id)
      notice.value = `流程运行 #${result.id} 已提交，按步骤在主控本机执行。`
      await Promise.all([refresh(true), openRun(result.id)])
    } catch (error) {
      if (session !== sessionSequence) return
      if (error.status && error.status < 500) pendingStarts.delete(flow.id)
      fail(error, actionError)
    } finally { if (session === sessionSequence) busy.value = '' }
  }

  async function confirmAction() {
    if (!live() || !isAdmin.value || !confirmation.value || busy.value) return
    const target = confirmation.value
    const stopping = target.type === 'stop'
    busy.value = `${target.type}:${target.id}`
    actionError.value = ''
    notice.value = ''
    const session = sessionSequence
    try {
      const result = await api(stopping ? `/workflow-runs/${target.id}/stop` : `/workflows/${target.id}/archive`, { method: 'POST', body: '{}' })
      if (!live() || session !== sessionSequence) return
      confirmation.value = null
      notice.value = stopping
        ? (result.status === 'cancelled' ? `流程运行 #${target.id} 已取消。` : `已请求停止流程运行 #${target.id}，等待当前步骤收尾。`)
        : '流程已归档，历史记录保留。'
      await refresh(true)
    } catch (error) { if (session === sessionSequence) fail(error, actionError) }
    finally { if (session === sessionSequence) busy.value = '' }
  }

  function askConfirmation(target) {
    if (!live() || !isAdmin.value) return
    actionError.value = ''
    confirmation.value = target
  }

  function clear(clearPending = false) {
    sessionSequence += 1
    listSequence += 1
    historySequence += 1
    detailSequence += 1
    candidateSequence += 1
    window.clearTimeout(pollTimer)
    controllers.forEach((controller) => controller.abort())
    workflows.value = []; runs.value = []; candidates.value = []
    definitionsLoaded.value = false
    if (clearPending) pendingStarts.clear()
    editor.value = null; confirmation.value = null; runDetail.value = null; selectedRunId.value = null
    Object.assign(form, { name: '', description: '', task_ids: [], ...emptyPlan() })
    pendingCreation = null; originalPlan = ''; formUncertain.value = false
    Object.assign(filters, { workflow_id: '', status: 'all', date_from: '', date_to: '' })
    appliedFilters = { ...filters }
    total.value = 0; page.value = 1; busy.value = ''; notice.value = ''; actionError.value = ''
    loading.value = true; refreshing.value = false; historyLoading.value = false
    detailLoading.value = false; candidatesLoading.value = false
    loadError.value = ''; historyError.value = ''; detailError.value = ''; formError.value = ''; candidatesError.value = ''
  }

  watch(me, (user, previous) => {
    if (user !== previous) {
      clear(true)
      if (user && active) refresh()
    }
  }, { flush: 'sync' })
  onMounted(() => refresh())
  onDeactivated(() => {
    active = false
    sessionSequence += 1
    listSequence += 1; historySequence += 1; detailSequence += 1; candidateSequence += 1
    window.clearTimeout(pollTimer)
    controllers.forEach((controller) => controller.abort())
    closeRun()
    confirmation.value = null
    busy.value = ''; refreshing.value = false; historyLoading.value = false; candidatesLoading.value = false
  })
  onActivated(() => { if (!active) { active = true; refresh(true) } })
  onBeforeUnmount(() => { disposed = true; clear() })
  return {
    workflows, visibleWorkflows, runs, total, page, pageSize, totalPages, loading, definitionsLoaded, refreshing, historyLoading,
    loadError, historyError, actionError, notice, busy, showArchived, filters, editor, form, formError, formUncertain,
    candidates, candidatesLoading, candidatesError, selectedTask, selectedSteps, selectedRunId, runDetail,
    detailLoading, detailError, confirmation, pendingStarts,
    refresh, loadHistory, applyFilters, resetFilters, openRun, closeRun, loadRunDetail, openEditor, closeEditor,
    addStep, moveStep, saveWorkflow, resolveCreation, startWorkflow, confirmAction, askConfirmation, loadCandidates,
  }
}
