import { computed, reactive, ref } from 'vue'
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
  const isSuperAdmin = computed(() => me.value?.role === 'super_admin')

  const isAdmin = computed(() => ['super_admin', 'admin'].includes(me.value?.role))

  const canChangePassword = computed(() => Boolean(me.value) && me.value.role !== 'admin')

  async function checkAuth() {
    authChecking.value = true
    try {
      me.value = await request('/auth/me')
      changePasswordOpen.value = false
      await loadAll()
    } catch (error) {
      if (error.status !== 401) showToast('error', '无法连接 SpiderFly', error.message)
      me.value = null
    } finally {
      authChecking.value = false
    }
  }

  async function login() {
    if (!loginForm.username.trim() || !loginForm.password) {
      showToast('error', '请输入账号和密码')
      return
    }
    loginBusy.value = true
    try {
      await request('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username: loginForm.username.trim(), password: loginForm.password }),
      })
      me.value = await request('/auth/me')
      loginForm.password = ''
      view.value = 'overview'
      changePasswordOpen.value = false
      await loadAll()
      showToast('success', '欢迎回来', me.value.display_name || me.value.username)
    } catch (error) {
      showToast('error', '登录失败', error.message)
    } finally {
      loginBusy.value = false
    }
  }

  async function logout() {
    try {
      await request('/auth/logout', { method: 'POST' })
    } catch {}
    me.value = null
    changePasswordOpen.value = false
    passwordForm.current_password = ''
    passwordForm.new_password = ''
    passwordForm.confirm_password = ''
    clearSharedData()
    stopPolling()
  }

  async function changePassword() {
    if (!canChangePassword.value) return
    if (!passwordForm.current_password || !passwordForm.new_password) {
      showToast('error', '请填写当前密码和新密码')
      return
    }
    if (passwordForm.new_password !== passwordForm.confirm_password) {
      showToast('error', '两次输入的新密码不一致')
      return
    }
    saving.value = true
    try {
      await request('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: passwordForm.current_password,
          new_password: passwordForm.new_password,
        }),
      })
      me.value = { ...me.value, must_change_password: false }
      changePasswordOpen.value = false
      passwordForm.current_password = ''
      passwordForm.new_password = ''
      passwordForm.confirm_password = ''
      showToast('success', '密码已更新')
      await loadAll()
    } catch (error) {
      showToast('error', '密码修改失败', error.message)
    } finally {
      saving.value = false
    }
  }

  return { isSuperAdmin, isAdmin, canChangePassword, checkAuth, login, logout, changePassword }
}
