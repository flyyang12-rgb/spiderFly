<script setup>
import { useWorkspaceContext } from '../../workspace/context'

const { changePassword, changePasswordOpen, passwordForm, saving } = useWorkspaceContext()
</script>

<template>
  <div class="modal-layer mandatory-layer" @mousedown.self="changePasswordOpen = false">
    <section class="modal password-modal" role="dialog" aria-modal="true" aria-label="修改密码">
      <header>
        <div>
          <span class="eyebrow">ACCOUNT SECURITY</span>
          <h2>修改登录密码</h2>
        </div>
        <button class="modal-close" type="button" aria-label="关闭" @click="changePasswordOpen = false">
          ×
        </button>
      </header>
      <div class="modal-body">
        <label class="field"
          ><span>当前密码</span
          ><input v-model="passwordForm.current_password" type="password" autocomplete="current-password"
        /></label>
        <label class="field"
          ><span>新密码</span
          ><input
            v-model="passwordForm.new_password"
            type="password"
            minlength="6"
            autocomplete="new-password"
          /><small>至少 6 个字符。</small></label
        >
        <label class="field"
          ><span>再次输入新密码</span
          ><input
            v-model="passwordForm.confirm_password"
            type="password"
            minlength="6"
            autocomplete="new-password"
        /></label>
      </div>
      <footer>
        <button class="button ghost" type="button" @click="changePasswordOpen = false">取消</button>
        <button class="button primary" type="button" :disabled="saving" @click="changePassword">
          {{ saving ? '正在保存…' : '保存新密码' }}
        </button>
      </footer>
    </section>
  </div>
</template>
