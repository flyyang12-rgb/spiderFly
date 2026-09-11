<script setup>
import { useWorkspaceContext } from '../../workspace/context'

const {
  createUser,
  creatingUser,
  deletingUser,
  isSuperAdmin,
  me,
  openUserEditor,
  refreshMembers,
  roleLabel,
  showToast,
  userForm,
  users,
  view,
} = useWorkspaceContext()
</script>

<template>
  <section class="view-stack">
    <section v-if="isSuperAdmin" class="panel">
      <header class="panel-heading">
        <div><h2>创建成员账号</h2></div>
        <span class="mini-badge neutral-badge">超级管理员区域</span>
      </header>
      <form class="user-create-form" @submit.prevent="createUser">
        <label class="field"
          ><span>登录账号</span
          ><input
            v-model="userForm.username"
            type="text"
            autocomplete="off"
            maxlength="50"
            placeholder="例如：xiaoming"
        /></label>
        <label class="field"
          ><span>显示名称</span
          ><input v-model="userForm.display_name" type="text" maxlength="100" placeholder="例如：小明"
        /></label>
        <label class="field"
          ><span>成员角色</span
          ><select v-model="userForm.role">
            <option value="operator">普通成员</option>
            <option value="admin">管理员</option>
          </select></label
        >
        <label class="field"
          ><span>初始密码</span
          ><input
            v-model="userForm.password"
            type="password"
            minlength="6"
            autocomplete="new-password"
            placeholder="至少 6 个字符"
        /></label>
        <button class="button primary" type="submit" :disabled="creatingUser">
          {{ creatingUser ? '正在创建…' : '创建成员' }}
        </button>
      </form>
    </section>
    <section class="panel table-panel">
      <header class="panel-heading">
        <div>
          <h2>团队成员</h2>
          <p>{{ users.length }} 个账号</p>
        </div>
        <button
          type="button"
          class="button secondary compact"
          @click="refreshMembers().catch((error) => showToast('error', '刷新失败', error.message))"
        >
          刷新成员
        </button>
      </header>
      <div v-if="users.length" class="member-grid">
        <article v-for="user in users" :key="user.id" class="member-card">
          <span class="avatar">{{ (user.display_name || user.username).slice(0, 1) }}</span>
          <div>
            <strong>{{ user.display_name || user.username }}</strong
            ><small>@{{ user.username }}</small>
          </div>
          <span class="mini-badge" :class="user.role !== 'operator' ? 'success-badge' : 'neutral-badge'">{{
            roleLabel(user.role)
          }}</span>
          <div class="member-actions">
            <small>{{ !user.active ? '已停用' : '正常' }}{{ user.id === me.id ? ' · 当前账号' : '' }}</small>
            <button
              v-if="isSuperAdmin"
              type="button"
              class="button secondary compact"
              @click="openUserEditor(user)"
            >
              编辑
            </button>
            <button
              v-if="isSuperAdmin && user.id !== me.id"
              type="button"
              class="button danger compact"
              @click="deletingUser = { ...user }"
            >
              删除
            </button>
          </div>
        </article>
      </div>
      <div v-else class="empty-state compact-empty"><strong>暂无成员记录</strong></div>
    </section>
  </section>
</template>
