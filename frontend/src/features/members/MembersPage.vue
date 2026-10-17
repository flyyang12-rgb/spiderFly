<script setup>
import { ref } from 'vue'
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
} = useWorkspaceContext()
const showCreate = ref(false)
const createTrigger = ref(null)
function hideCreate() {
  showCreate.value = false
  createTrigger.value?.focus()
}
</script>

<template>
  <section class="view-stack">
    <section class="panel table-panel members-panel">
      <header class="panel-heading">
        <div class="members-title"><h2>团队成员</h2><span>{{ users.length }}</span></div>
        <div class="members-heading-actions">
          <button type="button" class="button secondary compact" @click="refreshMembers().catch((error) => showToast('error', '刷新失败', error.message))">刷新</button>
          <button v-if="isSuperAdmin" ref="createTrigger" type="button" class="button compact" :class="showCreate ? 'secondary' : 'primary'" :disabled="creatingUser" :aria-expanded="showCreate" aria-controls="member-create-form" @click="showCreate = !showCreate">{{ showCreate ? '收起新建' : '新建成员' }}</button>
        </div>
      </header>
      <form v-if="isSuperAdmin" v-show="showCreate" id="member-create-form" class="member-create-form" aria-label="新建成员" @submit.prevent="createUser" @keydown.esc="!creatingUser && hideCreate()">
        <label class="field"
          ><span>登录账号</span
          ><input
            v-model="userForm.username"
            type="text"
            autocomplete="off"
            maxlength="50"
        /></label>
        <label class="field"
          ><span>显示名称</span
          ><input v-model="userForm.display_name" type="text" maxlength="100"
        /></label>
        <label class="field"
          ><span>角色</span
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
        <div class="member-create-actions"><button class="button ghost" type="button" :disabled="creatingUser" @click="hideCreate">取消</button><button class="button primary" type="submit" :disabled="creatingUser">{{ creatingUser ? '正在创建…' : '创建成员' }}</button></div>
      </form>
      <div v-if="users.length" class="members-list">
        <article v-for="user in users" :key="user.id" class="member-row">
          <span class="avatar" aria-hidden="true">{{ (user.display_name || user.username).slice(0, 1) }}</span>
          <div class="member-identity">
            <strong>{{ user.display_name || user.username }}</strong
            ><small>@{{ user.username }}</small>
          </div>
          <span class="mini-badge" :class="user.role !== 'operator' ? 'success-badge' : 'neutral-badge'">{{
            roleLabel(user.role)
          }}</span>
          <div class="member-row-actions">
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
      <div v-else class="empty-state compact-empty"><strong>暂无成员</strong></div>
    </section>
  </section>
</template>

<style scoped>
.members-title, .members-heading-actions { display: flex; align-items: center; gap: 8px; }
.members-title > span { color: var(--muted, #757a75); font-size: 12px; }
.member-create-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; padding: 20px; border-bottom: 1px solid var(--border, #dfe3df); background: var(--surface-subtle, #f7f8f6); }
.member-create-actions { grid-column: 1 / -1; display: flex; justify-content: flex-end; gap: 8px; }
.members-list { padding: 0 20px; }
.member-row { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; align-items: center; gap: 12px; min-height: 80px; padding: 16px 0; }
.member-row + .member-row { border-top: 1px solid var(--border, #ecefec); }
.member-identity { display: grid; min-width: 0; gap: 4px; }
.member-identity strong { color: var(--ink, #221814); font-size: 14px; overflow-wrap: anywhere; }
.member-identity small, .member-row-actions > small { color: var(--muted, #757a75); font-size: 12px; overflow-wrap: anywhere; }
.member-row-actions { display: flex; align-items: center; gap: 8px; }
.member-row-actions > small { margin-right: 8px; }
@media (max-width: 760px) { .member-row { grid-template-columns: auto minmax(0, 1fr) auto; } .member-row-actions { grid-column: 2 / -1; flex-wrap: wrap; } }
@media (max-width: 480px) { .member-create-form { grid-template-columns: 1fr; padding: 16px; } .members-list { padding: 0 16px; } .members-heading-actions { flex-wrap: wrap; justify-content: flex-end; } .members-panel > .panel-heading { flex-wrap: wrap; } .member-row { gap: 8px; } .member-row-actions > small { flex-basis: 100%; } }
</style>
