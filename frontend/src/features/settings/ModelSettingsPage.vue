<script setup>
import { useWorkspaceContext } from '../../workspace/context'
import AiSettings from '../../AiSettings.vue'

const { isSuperAdmin, view } = useWorkspaceContext()
</script>

<template>
  <section class="view-stack">
    <AiSettings :can-edit="isSuperAdmin" />
  </section>
</template>
