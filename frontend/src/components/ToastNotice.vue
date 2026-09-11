<script setup>
import { useWorkspaceContext } from '../workspace/context'

const { toast } = useWorkspaceContext()
</script>

<template>
  <div class="toast" :class="toast.type" role="status">
    <span class="toast-icon">{{ toast.type === 'success' ? '✓' : '!' }}</span>
    <span
      ><strong>{{ toast.title }}</strong
      ><small v-if="toast.message">{{ toast.message }}</small></span
    >
    <button type="button" aria-label="关闭通知" @click="toast.visible = false">×</button>
  </div>
</template>
