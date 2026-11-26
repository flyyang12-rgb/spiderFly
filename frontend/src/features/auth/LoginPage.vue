<script setup>
import { useWorkspaceContext } from '../../workspace/context'

const { login, loginBusy, loginForm } = useWorkspaceContext()
</script>

<template>
  <div class="auth-screen">
    <section class="login-card">
      <div class="login-brand">
        <div class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></div>
        <div><strong>SpiderFly</strong></div>
      </div>
      <div class="login-copy">
        <h1>登录</h1>
      </div>
      <form class="login-form" @submit.prevent="login">
        <label class="field">
          <span>账号</span>
          <input
            v-model="loginForm.username"
            type="text"
            autocomplete="username"
            autofocus
          />
        </label>
        <label class="field">
          <span>密码</span>
          <input
            v-model="loginForm.password"
            type="password"
            autocomplete="current-password"
          />
        </label>
        <button class="button primary login-button" type="submit" :disabled="loginBusy">
          {{ loginBusy ? '正在登录…' : '登录' }}
        </button>
      </form>
    </section>
  </div>
</template>

<style scoped>
.auth-screen { background: #f7f8f6; }
.auth-screen::before { display: none; }
.login-card { gap: 26px; padding: 36px; border: 1px solid #dfe3df; border-radius: 12px; background: #fff; box-shadow: none; backdrop-filter: none; }
.login-brand { gap: 10px; }
.login-brand strong { font-size: 22px; font-weight: 600; line-height: 1.25; letter-spacing: -.035em; }
.login-copy h1 { margin: 0; color: var(--ink); font-size: 32px; line-height: 1.3; font-weight: 500; letter-spacing: normal; }
.login-form { --field-height: 44px; --field-input-size: 16px; gap: 22px; }
.login-form .field { gap: 6px; }
.login-button { font-size: 15px; }
@media (max-width: 480px) { .auth-screen { padding: 16px; } .login-card { padding: 24px; } }
</style>
