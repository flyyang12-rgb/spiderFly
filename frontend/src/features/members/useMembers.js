import { reactive, ref } from 'vue'
import { request } from '../../lib/api'

export function createMembersState() {
  const users = ref([])
  const auditLogs = ref([])
  const creatingUser = ref(false)
  const editingUser = ref(null)
  const deletingUser = ref(null)
  const savingUser = ref(false)
  const deletingUserBusy = ref(false)
  const userForm = reactive({ username: '', display_name: '', role: 'operator', password: '123321' })
  const userEditForm = reactive({
    username: '',
    display_name: '',
    role: 'operator',
    active: true,
    password: '',
    confirm_password: '',
  })
  return {
    users,
    auditLogs,
    creatingUser,
    editingUser,
    deletingUser,
    savingUser,
    deletingUserBusy,
    userForm,
    userEditForm,
  }
}

export function useMembers({
  auditLogs,
  creatingUser,
  deletingUser,
  deletingUserBusy,
  editingUser,
  isAdmin,
  isSuperAdmin,
  logout,
  me,
  savingUser,
  showToast,
  userEditForm,
  userForm,
  users,
}) {
  async function createUser() {
    if (!isSuperAdmin.value) return
    if (!userForm.username.trim() || !userForm.display_name.trim() || !userForm.password) {
      showToast('error', '请完整填写成员信息')
      return
    }
    creatingUser.value = true
    try {
      await request('/users', {
        method: 'POST',
        body: JSON.stringify({
          username: userForm.username.trim(),
          display_name: userForm.display_name.trim(),
          role: userForm.role,
          password: userForm.password,
        }),
      })
      Object.assign(userForm, { username: '', display_name: '', role: 'operator', password: '123321' })
      showToast('success', '成员账号已创建')
      const results = await Promise.all([request('/users'), request('/audit-logs?limit=100')])
      users.value = results[0]
      auditLogs.value = results[1]
    } catch (error) {
      showToast('error', '账号创建失败', error.message)
    } finally {
      creatingUser.value = false
    }
  }

  function openUserEditor(user) {
    if (!isSuperAdmin.value) return
    editingUser.value = { ...user }
    Object.assign(userEditForm, {
      username: user.username,
      display_name: user.display_name,
      role: user.role,
      active: user.active,
      password: '',
      confirm_password: '',
    })
  }

  function closeUserEditor() {
    if (savingUser.value) return
    editingUser.value = null
    userEditForm.password = ''
    userEditForm.confirm_password = ''
  }

  async function refreshMembers() {
    if (!isAdmin.value) return
    const result = await Promise.all([request('/users'), request('/audit-logs?limit=100')])
    users.value = result[0]
    auditLogs.value = result[1]
  }

  async function saveUser() {
    if (!editingUser.value || savingUser.value || !isSuperAdmin.value) return
    if (userEditForm.password !== userEditForm.confirm_password) {
      showToast('error', '两次输入的密码不一致')
      return
    }
    const target = editingUser.value
    const payload = {
      version: target.version,
      username: userEditForm.username.trim(),
      display_name: userEditForm.display_name.trim(),
    }
    if (target.id !== me.value.id) {
      payload.role = userEditForm.role
      payload.active = userEditForm.active
      if (userEditForm.password) payload.password = userEditForm.password
    }
    savingUser.value = true
    try {
      const updated = await request('/users/' + target.id, { method: 'PATCH', body: JSON.stringify(payload) })
      editingUser.value = null
      userEditForm.password = ''
      userEditForm.confirm_password = ''
      if (target.id === me.value.id && updated.username !== me.value.username) {
        await logout()
        showToast('success', '账号已修改', '请使用新账号和原密码重新登录')
        return
      }
      if (target.id === me.value.id) me.value = updated
      showToast('success', '成员信息已保存', payload.password ? '密码已重置，该成员可直接使用新密码登录' : '')
      await refreshMembers()
    } catch (error) {
      showToast('error', '成员修改失败', error.message)
    } finally {
      savingUser.value = false
    }
  }

  async function confirmDeleteUser() {
    if (!deletingUser.value || deletingUserBusy.value || !isSuperAdmin.value) return
    deletingUserBusy.value = true
    try {
      await request('/users/' + deletingUser.value.id + '?version=' + deletingUser.value.version, {
        method: 'DELETE',
      })
      deletingUser.value = null
      showToast('success', '成员已删除', '账号已禁止登录，历史记录保留')
      await refreshMembers()
    } catch (error) {
      showToast('error', '成员删除失败', error.message)
    } finally {
      deletingUserBusy.value = false
    }
  }

  return { createUser, openUserEditor, closeUserEditor, refreshMembers, saveUser, confirmDeleteUser }
}
