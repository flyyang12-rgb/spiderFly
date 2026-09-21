import { reactive, ref } from 'vue'
import { triggerConfig } from '../../lib/format'
import { request } from '../../lib/api'

export function createAppsState() {
  const appForm = reactive({
    name: '',
    description: '',
    requirements_text: '',
    requirements_filename: '',
    script: null,
    enabled: true,
    notify_on_success: true,
    notify_on_failure: true,
    failure_screenshot: false,
    trigger_type: 'manual',
    target_host_id: '',
    daily_time: '09:00',
    weekly_days: [1],
    weekly_time: '09:00',
  })
  const uploading = ref(false)
  const uploadKey = ref(0)
  const aiOpen = ref(false)
  const aiTaskId = ref(null)
  const aiSourceThread = ref(null)
  return { appForm, uploading, uploadKey, aiOpen, aiTaskId, aiSourceThread }
}

export function useApps({
  aiOpen,
  aiSourceThread,
  aiTaskId,
  appForm,
  isAdmin,
  loadAll,
  navigateTo,
  showToast,
  uploadKey,
  uploading,
}) {
  function selectScript(event) {
    aiSourceThread.value = null
    appForm.script = event.target.files?.[0] || null
    if (!appForm.name && appForm.script) appForm.name = appForm.script.name.replace(/\.py$/i, '')
  }

  async function selectRequirementsFile(event) {
    const input = event.currentTarget
    const file = input.files?.[0]
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.txt')) {
      input.value = ''
      showToast('error', '依赖文件格式不对', '请选择 requirements.txt 文本文件')
      return
    }
    if (file.size > 100 * 1024) {
      input.value = ''
      showToast('error', '依赖文件过大', 'requirements.txt 内容不能超过 20,000 个字符')
      return
    }
    try {
      const text = new TextDecoder('utf-8', { fatal: true })
        .decode(await file.arrayBuffer())
        .replace(/^\uFEFF/, '')
        .replace(/\r\n?/g, '\n')
        .trim()
      if (!text) throw new Error('requirements.txt 是空文件')
      if (text.length > 20000) throw new Error('requirements.txt 内容不能超过 20,000 个字符')
      if (text.includes('\0')) throw new Error('requirements.txt 不是有效的文本文件')
      appForm.requirements_text = text
      appForm.requirements_filename = file.name
      showToast('success', '依赖清单已读取', '确认后点击“创建任务”')
    } catch (error) {
      input.value = ''
      appForm.requirements_filename = ''
      showToast('error', '无法读取依赖文件', error.message || '请确认文件使用 UTF-8 编码')
    }
  }

  function openAi(task = null) {
    aiTaskId.value = task?.id || null
    aiOpen.value = true
  }

  function prepareAiDraft(draft) {
    Object.assign(appForm, {
      name: (draft.name + (draft.task_id ? ' · AI试运行' : '')).slice(0, 100),
      description: draft.description.slice(0, 500),
      requirements_text: draft.requirements,
      requirements_filename: 'AI 生成的 requirements.txt',
      script: new File([draft.source], 'main.py', { type: 'text/x-python' }),
      trigger_type: 'manual',
      target_host_id: '',
      enabled: true,
      notify_on_success: false,
      notify_on_failure: false,
      failure_screenshot: false,
    })
    aiSourceThread.value = draft.task_id ? null : draft.thread_id
    uploadKey.value += 1
    aiOpen.value = false
    navigateTo('management', 'apps')
    showToast(
      'success',
      'Python 草稿已带入',
      draft.trial?.status === 'success'
        ? '采集试跑通过，可创建任务并设置定时'
        : '语法已检查，请先创建手动任务验证实际结果',
    )
  }

  async function uploadApp() {
    if (!isAdmin.value) return
    if (!appForm.name.trim() || !appForm.script) {
      showToast('error', '请选择 Python 脚本并填写任务名称')
      return
    }
    if (!appForm.script.name.toLowerCase().endsWith('.py')) {
      showToast('error', '只能上传 .py 文件')
      return
    }
    if (appForm.trigger_type === 'weekly' && !appForm.weekly_days.length) {
      showToast('error', '请至少选择一个星期')
      return
    }
    if (appForm.trigger_type === 'daily' && !appForm.daily_time) {
      showToast('error', '请选择每天执行时间')
      return
    }
    if (appForm.trigger_type === 'weekly' && !appForm.weekly_time) {
      showToast('error', '请选择每周执行时间')
      return
    }
    uploading.value = true
    try {
      const form = new FormData()
      form.append('name', appForm.name.trim())
      form.append('description', appForm.description.trim())
      form.append('requirements_text', appForm.requirements_text.trim())
      form.append('trigger_type', appForm.trigger_type)
      if (appForm.target_host_id) form.append('target_host_id', appForm.target_host_id)
      form.append('trigger_config', JSON.stringify(triggerConfig(appForm)))
      form.append('enabled', String(appForm.enabled))
      form.append('notify_on_success', String(appForm.notify_on_success))
      form.append('notify_on_failure', String(appForm.notify_on_failure))
      form.append('failure_screenshot', String(appForm.failure_screenshot))
      form.append('script', appForm.script)
      const created = await request('/apps', { method: 'POST', body: form })
      let aiBindingError = ''
      if (aiSourceThread.value && created.task?.id) {
        try {
          await request(`/ai/threads/${aiSourceThread.value}/bind`, {
            method: 'POST',
            body: JSON.stringify({ task_id: created.task.id }),
          })
        } catch (error) {
          aiBindingError = error.message
        }
      }
      aiSourceThread.value = null
      Object.assign(appForm, {
        name: '',
        description: '',
        requirements_text: '',
        requirements_filename: '',
        script: null,
        enabled: true,
        notify_on_success: true,
        notify_on_failure: true,
        failure_screenshot: false,
        trigger_type: 'manual',
        target_host_id: '',
        daily_time: '09:00',
        weekly_days: [1],
        weekly_time: '09:00',
      })
      uploadKey.value += 1
      await loadAll({ quiet: true, includeAdmin: false })
      navigateTo('tasks')
      if (aiBindingError) showToast('error', '任务已创建，AI 对话关联未完成', aiBindingError)
      else showToast('success', '任务已创建为 v1', '上传阶段没有运行代码，请选择宿主机进行实际运行')
    } catch (error) {
      showToast('error', '创建任务失败', error.message)
    } finally {
      uploading.value = false
    }
  }

  async function rebuildTaskEnvironment(task) {
    if (!isAdmin.value) return
    try {
      await request('/apps/' + task.app_id + '/rebuild', { method: 'POST' })
      showToast('success', '正在修复运行环境', task.name)
      await loadAll({ quiet: true, includeAdmin: false })
    } catch (error) {
      showToast('error', '无法修复运行环境', error.message)
    }
  }

  function toggleCreateWeekday(day) {
    appForm.weekly_days = appForm.weekly_days.includes(day)
      ? appForm.weekly_days.filter((item) => item !== day)
      : [...appForm.weekly_days, day].sort()
  }

  return {
    selectScript,
    selectRequirementsFile,
    openAi,
    prepareAiDraft,
    uploadApp,
    rebuildTaskEnvironment,
    toggleCreateWeekday,
  }
}
