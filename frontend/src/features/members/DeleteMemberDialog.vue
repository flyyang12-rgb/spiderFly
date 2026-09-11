<script setup>
import { useWorkspaceContext } from '../../workspace/context'

const { confirmDeleteUser, deletingUser, deletingUserBusy } = useWorkspaceContext()
</script>

<template>
  <div class="modal-layer" @mousedown.self="!deletingUserBusy && (deletingUser = null)">
    <section class="modal confirm-modal" role="alertdialog" aria-modal="true" aria-label="删除成员">
      <header>
        <h2>删除成员“{{ deletingUser.display_name }}”</h2>
        <button
          type="button"
          class="modal-close"
          aria-label="关闭"
          :disabled="deletingUserBusy"
          @click="deletingUser = null"
        >
          ×
        </button>
      </header>
      <div class="modal-body confirm-copy">
        <p>
          账号 <strong>{{ deletingUser.username }}</strong> 将立即禁止登录，并从成员列表移除。
        </p>
        <p>历史任务、执行记录和审计记录保留；原账号名继续保留，不能重新注册。</p>
      </div>
      <footer>
        <button type="button" class="button ghost" :disabled="deletingUserBusy" @click="deletingUser = null">
          取消</button
        ><button type="button" class="button danger" :disabled="deletingUserBusy" @click="confirmDeleteUser">
          {{ deletingUserBusy ? '正在删除…' : '确认删除成员' }}
        </button>
      </footer>
    </section>
  </div>
</template>
