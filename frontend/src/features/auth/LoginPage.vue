<script setup>
import { useWorkspaceContext } from '../../workspace/context'

const { login, loginBusy, loginForm } = useWorkspaceContext()
</script>

<template>
  <div class="auth-screen">
    <div class="auth-ornament auth-ornament-one"></div>
    <div class="auth-ornament auth-ornament-two"></div>
    <section class="login-card">
      <div class="login-brand">
        <div class="brand-mark large" aria-hidden="true"><span></span><span></span><span></span></div>
        <div><strong>SpiderFly</strong><small>团队 Python 自动化控制台</small></div>
      </div>
      <div class="login-copy">
        <h1>欢迎回来</h1>
      </div>
      <form class="login-form" @submit.prevent="login">
        <label class="field">
          <span>账号</span>
          <input
            v-model="loginForm.username"
            type="text"
            autocomplete="username"
            autofocus
            placeholder="请输入用户名"
          />
        </label>
        <label class="field">
          <span>密码</span>
          <input
            v-model="loginForm.password"
            type="password"
            autocomplete="current-password"
            placeholder="请输入密码"
          />
        </label>
        <button class="button primary login-button" type="submit" :disabled="loginBusy">
          {{ loginBusy ? '正在登录…' : '登录共享任务中心' }}
        </button>
      </form>
    </section>
  </div>
</template>
