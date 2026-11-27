import { reactive, ref, watch } from 'vue'
import { request } from '../../lib/api'
import { emptyPlan, planBody, validatePlan, creationRequestId } from '../../lib/plans'

function emptyForm() {
  return { name: '', description: '', requirements_text: '', requirements_filename: '', script: null, project_entry: '', project_entries: [], project_loading: false,
    dependency_decisions: [], dependency_status: '',
    enabled: true, notify_on_success: true, notify_on_failure: true, failure_screenshot: false,
    ...emptyPlan(), target_host_id: '' }
}

export function createAppsState() {
  return { appForm: reactive(emptyForm()), uploading: ref(false), uploadKey: ref(0), uploadError: ref(''),
    uploadUncertain: ref(false), requirementsReading: ref(false), aiOpen: ref(false), aiTaskId: ref(null), aiSourceThread: ref(null) }
}

export function useApps({ aiOpen, aiSourceThread, aiTaskId, appForm, isAdmin, loadAll, navigateTo, openCreate,
  showToast, uploadKey, uploading, me, handleSessionExpired, uploadError = ref(''), uploadUncertain = ref(false), requirementsReading = ref(false) }) {
  let session = 0
  let requirementsSequence = 0
  let completedTask = null
  let pendingUpload = null
  const controllers = new Set()
  function live(generation, owner) { return generation === session && (!me || Boolean(owner) && me.value === owner) }
  async function api(path, options = {}) {
    const controller = new AbortController()
    controllers.add(controller)
    try { return await request(path, { ...options, signal: controller.signal }) }
    finally { controllers.delete(controller) }
  }
  function resetCreateSession() {
    session += 1
    requirementsSequence += 1
    controllers.forEach(controller => controller.abort())
    Object.assign(appForm, emptyForm())
    aiSourceThread.value = null
    uploadKey.value += 1
    uploading.value = false
    requirementsReading.value = false
    uploadError.value = ''
    uploadUncertain.value = false
    completedTask = null
    pendingUpload = null
  }
  if (me) watch(me, (user, previous) => { if (user !== previous) resetCreateSession() }, { flush: 'sync' })

  async function selectScript(event) {
    if (uploading.value || uploadUncertain.value) return
    aiSourceThread.value = null
    const generation = session
    const owner = me?.value
    const file = event.target.files?.[0] || null
    appForm.requirements_text = ''
    appForm.requirements_filename = ''
    appForm.script = file
    appForm.project_entry = ''
    appForm.project_entries = []
    appForm.project_loading = false
    appForm.dependency_status = ''
    appForm.dependency_decisions = []
    if (!appForm.name && file) appForm.name = file.name.replace(/\.(py|zip)$/i, '')
    uploadError.value = ''
    if (!file || !/\.zip$/i.test(file.name)) return
    appForm.dependency_status = ''
    appForm.project_loading = true
    const body = new FormData()
    body.append('project_archive', file)
    try {
      const result = await api('/apps/project-entries', { method: 'POST', body })
      if (!live(generation, owner) || appForm.script !== file) return
      appForm.project_entries = result.entries
      if (result.entries.length === 1) appForm.project_entry = result.entries[0]
    } catch (error) {
      if (!live(generation, owner) || appForm.script !== file) return
      uploadError.value = error.message || '无法读取项目入口'
      if (error.status === 401) handleSessionExpired()
    } finally {
      if (live(generation, owner) && appForm.script === file) appForm.project_loading = false
    }
  }

  async function selectRequirementsFile(event) {
    if (uploading.value || uploadUncertain.value) return
    const input = event.currentTarget
    const file = input.files?.[0]
    const generation = session
    const owner = me?.value
    const sequence = ++requirementsSequence
    requirementsReading.value = false
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.txt') || file.size > 100 * 1024) {
      input.value = ''
      uploadError.value = file.size > 100 * 1024 ? 'requirements.txt 文件过大，内容不能超过 20,000 个字符' : '请选择 requirements.txt 文本文件'
      return
    }
    requirementsReading.value = true
    try {
      const text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())
        .replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').trim()
      if (!live(generation, owner) || sequence !== requirementsSequence) return
      if (!text) throw new Error('requirements.txt 是空文件')
      if (text.length > 20000) throw new Error('requirements.txt 内容不能超过 20,000 个字符')
      if (text.includes('\0')) throw new Error('requirements.txt 不是有效的文本文件')
      appForm.requirements_text = text
      appForm.requirements_filename = file.name
      uploadError.value = ''
    } catch (error) {
      if (!live(generation, owner) || sequence !== requirementsSequence) return
      input.value = ''
      uploadError.value = error.message || '请确认依赖文件使用 UTF-8 编码'
    } finally { if (live(generation, owner) && sequence === requirementsSequence) requirementsReading.value = false }
  }

  function openAi(task = null) { aiTaskId.value = task?.id || null; aiOpen.value = true }
  function prepareAiDraft(draft) {
    if (!isAdmin.value || uploading.value) return
    resetCreateSession()
    Object.assign(appForm, { name: (String(draft.name || '') + (draft.task_id ? ' · AI试运行' : '')).slice(0, 100),
      description: String(draft.description || '').slice(0, 500), requirements_text: draft.requirements || '',
      requirements_filename: 'AI 生成的 requirements.txt', script: new File([draft.source || ''], 'main.py', { type: 'text/x-python' }),
      trigger_type: 'manual', target_host_id: '', enabled: true, notify_on_success: false, notify_on_failure: false, failure_screenshot: false })
    aiSourceThread.value = draft.task_id ? null : draft.thread_id
    uploadKey.value += 1
    aiOpen.value = false
    if (openCreate) openCreate({ preserveDraft: true })
    else navigateTo('management', 'apps')
    showToast('success', 'Python 草稿已带入', draft.trial?.status === 'success' ? '可在本表单选择手动或定时' : '语法已检查，实际结果需运行验证')
  }

  async function uploadApp({ retryUncertain = false } = {}) {
    if (!isAdmin.value || uploading.value || (uploadUncertain.value && !retryUncertain) || requirementsReading.value || (me && !me.value)) return null
    if (completedTask) return completedTask
    uploadError.value = ''
    if (!appForm.name.trim() || !appForm.script) { uploadError.value = '请选择脚本或项目并填写任务名称'; return null }
    const project = /\.zip$/i.test(appForm.script.name)
    if (!project && !/\.py$/i.test(appForm.script.name)) { uploadError.value = '请选择 .py 或项目 .zip'; return null }
    if (project && (!appForm.project_entry || appForm.project_loading)) {
      uploadError.value = '请选择项目的 Python 入口'
      return null
    }
    if (!pendingUpload && appForm.dependency_status && appForm.dependency_status !== 'ready') {
      uploadError.value = appForm.dependency_status === 'loading' ? '依赖识别尚未完成' : '请先处理依赖分析中的待补充项'; return null
    }
    const planError = validatePlan(appForm, Boolean(appForm.target_host_id))
    if (planError) { uploadError.value = planError; return null }
    const generation = session
    const owner = me?.value
    const sourceThread = aiSourceThread.value
    const form = pendingUpload?.form || new FormData()
    if (!pendingUpload) {
    form.append('name', appForm.name.trim())
    form.append('description', appForm.description.trim())
    form.append('requirements_text', appForm.requirements_text.trim())
    form.append('dependency_decisions', JSON.stringify(appForm.dependency_decisions || []))
    const plan = planBody(appForm)
    form.append('trigger_type', plan.trigger_type)
    form.append('trigger_config', JSON.stringify(plan.trigger_config))
    if (appForm.target_host_id) form.append('target_host_id', appForm.target_host_id)
    for (const key of ['enabled', 'notify_on_success', 'notify_on_failure', 'failure_screenshot']) form.append(key, String(appForm[key]))
    form.append(project ? 'project_archive' : 'script', appForm.script)
    if (project) form.append('project_entry', appForm.project_entry)
    form.append('request_id', creationRequestId())
    pendingUpload = { form }
    }
    uploading.value = true
    try {
      const created = await api('/apps', { method: 'POST', body: form })
      if (!live(generation, owner)) return null
      if (!created?.task?.id) throw new Error('保存结果未确认，请刷新列表核对，避免重复创建。')
      completedTask = { kind: 'task', id: Number(created.task.id), object: created.task }
      let aiBindingError = ''
      if (sourceThread) {
        try { await api(`/ai/threads/${sourceThread}/bind`, { method: 'POST', body: JSON.stringify({ task_id: created.task.id }) }) }
        catch (error) { if (error.name !== 'AbortError') aiBindingError = error.message; if (error.status === 401) handleSessionExpired?.() }
      }
      if (!live(generation, owner)) return null
      aiSourceThread.value = null
      // The upload has already committed. Refresh/binding failures must never cause a second POST.
      try { await loadAll({ quiet: true, includeAdmin: false }) }
      catch (error) { if (live(generation, owner)) showToast('error', '任务已创建，列表刷新未完成', error.message) }
      if (!live(generation, owner)) return null
      if (aiBindingError) showToast('error', '任务已创建，AI 对话关联未完成', aiBindingError)
      else {
        const environment = created.environment_status || created.task.environment_status
        const status = created.task.target_host_id ? '目标电脑将在领取运行后准备环境'
          : environment === 'ready' ? '运行环境已就绪' : environment === 'failed' ? '依赖安装失败，可在任务中重试' : '运行环境准备中'
        showToast(environment === 'failed' && !created.task.target_host_id ? 'error' : 'success', '任务已保存', status)
      }
      uploadUncertain.value = false
      return completedTask
    } catch (error) {
      if (!live(generation, owner) || error.name === 'AbortError') return null
      uploadUncertain.value = !error.status || error.status >= 500
      if (!uploadUncertain.value) pendingUpload = null
      uploadError.value = uploadUncertain.value
        ? '保存结果未确认，请先刷新列表检查，避免重复上传。' + (error.message ? ' ' + error.message : '')
        : error.message || '创建任务失败，请检查输入'
      if (error.status === 401) handleSessionExpired?.()
      return null
    } finally { if (live(generation, owner)) uploading.value = false }
  }

  async function resolveUpload() {
    if (!pendingUpload || !uploadUncertain.value || uploading.value || !isAdmin.value) return null
    const generation = session, owner = me?.value
    uploading.value = true
    try {
      const result = await api('/creation-requests/task/' + pendingUpload.form.get('request_id'))
      if (!live(generation, owner)) return null
      completedTask = result
      uploadUncertain.value = false; uploadError.value = ''
      if (aiSourceThread.value) {
        try { await api(`/ai/threads/${aiSourceThread.value}/bind`, { method: 'POST', body: JSON.stringify({ task_id: result.id }) }) }
        catch (error) { if (live(generation, owner)) showToast('error', '任务已保存，AI 对话关联未完成', error.message) }
      }
      if (!live(generation, owner)) return null
      aiSourceThread.value = null
      return result
    } catch (error) {
      if (live(generation, owner) && error.name !== 'AbortError') {
        uploadError.value = error.message || '尚未确认创建结果'
        if (error.status === 401) handleSessionExpired?.()
      }
      return null
    } finally { if (live(generation, owner)) uploading.value = false }
  }

  async function rebuildTaskEnvironment(task) {
    if (!isAdmin.value || (me && !me.value)) return
    const generation = session
    const owner = me?.value
    try {
      await api('/apps/' + task.app_id + '/rebuild', { method: 'POST' })
      if (!live(generation, owner)) return
      showToast('success', '正在修复运行环境', task.name)
      await loadAll({ quiet: true, includeAdmin: false })
    } catch (error) {
      if (!live(generation, owner) || error.name === 'AbortError') return
      if (error.status === 401) handleSessionExpired?.()
      else showToast('error', '无法修复运行环境', error.message)
    }
  }
  function toggleCreateWeekday(day) {
    appForm.weekly_days = appForm.weekly_days.includes(day) ? appForm.weekly_days.filter(item => item !== day) : [...appForm.weekly_days, day].sort()
  }
  return { selectScript, selectRequirementsFile, openAi, prepareAiDraft, uploadApp, rebuildTaskEnvironment,
    toggleCreateWeekday, resetCreateSession, resolveUpload, uploadError, uploadUncertain, requirementsReading }
}
