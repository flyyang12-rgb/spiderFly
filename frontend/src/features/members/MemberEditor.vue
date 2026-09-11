<script setup>
import { useWorkspaceContext } from '../../workspace/context'

const { closeUserEditor, editingUser, me, saveUser, savingUser, userEditForm } = useWorkspaceContext()
</script>

<template>
  <div class="modal-layer" @mousedown.self="closeUserEditor">
    <section class="modal password-modal" role="dialog" aria-modal="true" aria-label="编辑成员">
      <header>
        <div>
          <span class="eyebrow">MEMBER</span>
          <h2>编辑成员</h2>
        </div>
        <button
          type="button"
          class="modal-close"
          aria-label="关闭"
          :disabled="savingUser"
          @click="closeUserEditor"
        >
          ×
        </button>
      </header>
      <form @submit.prevent="saveUser">
        <div class="modal-body">
          <div class="field-grid">
            <label class="field"
              ><span>登录账号</span
              ><input v-model="userEditForm.username" required maxlength="50" autocomplete="off"
            /></label>
            <label class="field"
              ><span>显示名称</span><input v-model="userEditForm.display_name" required maxlength="100"
            /></label>
            <label class="field"
              ><span>成员角色</span
              ><select v-model="userEditForm.role" :disabled="editingUser.id === me.id">
                <option v-if="editingUser.role === 'super_admin'" value="super_admin">超级管理员</option>
                <option value="admin">管理员</option>
                <option value="operator">普通成员</option>
              </select></label
            >
            <label class="field"
              ><span>账号状态</span
              ><select v-model="userEditForm.active" :disabled="editingUser.id === me.id">
                <option :value="true">启用</option>
                <option :value="false">停用</option>
              </select></label
            >
          </div>
          <template v-if="editingUser.id !== me.id">
            <label class="field"
              ><span>重置密码</span
              ><input
                v-model="userEditForm.password"
                type="password"
                minlength="6"
                maxlength="200"
                autocomplete="new-password"
                placeholder="留空则保留原密码"
              /><small>至少 6 个字符</small></label
            >
            <label class="field"
              ><span>确认密码</span
              ><input
                v-model="userEditForm.confirm_password"
                type="password"
                maxlength="200"
                autocomplete="new-password"
                placeholder="重置密码时再次输入"
            /></label>
          </template>
          <p v-else>当前账号不能降权、停用或删除。自己的密码请通过右上角“修改密码”调整。</p>
        </div>
        <footer>
          <button type="button" class="button ghost" :disabled="savingUser" @click="closeUserEditor">
            取消</button
          ><button type="submit" class="button primary" :disabled="savingUser">
            {{ savingUser ? '正在保存…' : '保存成员' }}
          </button>
        </footer>
      </form>
    </section>
  </div>
</template>
