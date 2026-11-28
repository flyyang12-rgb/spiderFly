<script setup>
import { useWorkspaceContext } from '../workspace/context'

const { toast, dismissToast, openToastAction } = useWorkspaceContext()
</script>

<template>
  <Teleport to="body">
    <div class="toast" :class="toast.type" role="status">
      <span class="toast-icon" aria-hidden="true">{{ toast.type === 'success' ? '✓' : toast.type === 'error' ? '!' : 'i' }}</span>
      <span
        ><strong>{{ toast.title }}</strong
        ><small v-if="toast.message">{{ toast.message }}</small>
        <button v-if="toast.actionLabel" type="button" class="toast-action" @click="openToastAction">{{ toast.actionLabel }}</button>
        <small v-if="toast.remaining">还有 {{ toast.remaining }} 条提示</small></span
      >
      <button type="button" class="toast-close" aria-label="关闭通知" @click="dismissToast">×</button>
    </div>
  </Teleport>
</template>
