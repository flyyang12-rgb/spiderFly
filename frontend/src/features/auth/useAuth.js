import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { request } from '../../lib/api'

export function createAuthState() {
  const authChecking = ref(true)
  const me = ref(null)
  const changePasswordOpen = ref(false)
  const loginBusy = ref(false)
  const loginForm = reactive({ username: '', password: '' })
  const passwordForm = reactive({ current_password: '', new_password: '', confirm_password: '' })
  return { authChecking, me, changePasswordOpen, loginBusy, loginForm, passwordForm }
}

export function useAuth({
  authChecking,
  changePasswordOpen,
  clearSharedData,
  handleSessionExpired,
  loadAll,
  loginBusy,
  loginForm,
  me,
  passwordForm,
  saving,
  showToast,
  stopPolling,
  view,
}) {
  let logoutRequest = null
  let authMutation = Promise.resolve()
  let generation = 0
  let disposed = false
  const current = ticket => !disposed && ticket === generation
  function mutateSession(path, options) {
    const pending = authMutation.catch(() => {}).then(() => request(path, options))
    authMutation = pending
    return pending
  }
  onBeforeUnmount(() => { disposed = true; generation++ })
  const isSuperAdmin = computed(() => me.value?.role === 'super_admin')

  const isAdmin = computed(() => ['super_admin', 'admin'].includes(me.value?.role))

  const canChangePassword = computed(() => Boolean(me.value) && me.value.role !== 'admin')

  async function checkAuth() {
    const ticket = ++generation
    authChecking.value = true
    try {
      const user = await request('/auth/me')
      if (!current(ticket)) return
      me.value = user
      changePasswordOpen.value = false
      await loadAll()
    } catch (error) {
      if (!current(ticket)) return
      if (error.status !== 401) showToast('error', '无法连接 SpiderFly', error.message)
      me.value = null
    } finally {
      if (current(ticket)) authChecking.value = false
    }
  }

  async function login() {
    if (loginBusy.value) return
    if (!loginForm.username.trim() || !loginForm.password) {
      showToast('error', '请输入账号和密码')
      return
    }
    loginBusy.value = true
    const ticket = ++generation
    try {
      await logoutRequest
      if (!current(ticket)) return
      await mutateSession('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username: loginForm.username.trim(), password: loginForm.password }),
      })
      if (!current(ticket)) return
      const user = await request('/auth/me')
      if (!current(ticket)) return
      me.value = user
      const owner = me.value
      loginForm.password = ''
      view.value = 'overview'
      changePasswordOpen.value = false
      await loadAll()
      if (!current(ticket) || me.value !== owner) return
      showToast('success', '欢迎回来', me.value.display_name || me.value.username)
    } catch (error) {
      if (current(ticket)) showToast('error', '登录失败', error.message)
    } finally {
      if (current(ticket)) loginBusy.value = false
    }
  }

  async function logout() {
    generation++
    authChecking.value = false
    loginBusy.value = false
    me.value = null
    changePasswordOpen.value = false
    passwordForm.current_password = ''
    passwordForm.new_password = ''
    passwordForm.confirm_password = ''
    clearSharedData()
    stopPolling()
    const pending = mutateSession('/auth/logout', { method: 'POST' }).catch(() => {})
    logoutRequest = pending
    await pending
    if (logoutRequest === pending) logoutRequest = null
  }

  async function changePassword() {
    if (!canChangePassword.value || saving.value) return
    if (!passwordForm.current_password || !passwordForm.new_password) {
      showToast('error', '请填写当前密码和新密码')
      return
    }
    if (passwordForm.new_password !== passwordForm.confirm_password) {
      showToast('error', '两次输入的新密码不一致')
      return
    }
    saving.value = true
    const ticket = generation
    let owner = me.value
    const valid = () => current(ticket) && Boolean(owner) && me.value === owner
    try {
      await request('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: passwordForm.current_password,
          new_password: passwordForm.new_password,
        }),
      })
      if (!valid()) return
      me.value = { ...owner, must_change_password: false }
      owner = me.value
      changePasswordOpen.value = false
      passwordForm.current_password = ''
      passwordForm.new_password = ''
      passwordForm.confirm_password = ''
      showToast('success', '密码已更新')
      await loadAll()
    } catch (error) {
      if (!valid()) return
      if (error.status === 401) handleSessionExpired?.()
      else showToast('error', '密码修改失败', error.message)
    } finally {
      if (valid()) saving.value = false
    }
  }

  return { isSuperAdmin, isAdmin, canChangePassword, checkAuth, login, logout, changePassword }
}
