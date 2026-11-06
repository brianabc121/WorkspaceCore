<script setup lang="ts">
import { useRoute } from 'vue-router'
import { computed } from 'vue'
import { useAuthStore } from '../stores/auth'
import ProjectOverview from '../features/tasks/ProjectOverview.vue'
const auth = useAuthStore(), route = useRoute()
const id = computed(() => String(route.params.id ?? ''))
</script>
<template><ProjectOverview v-if="auth.workspaceId && id" :key="`${auth.user?.id}:${auth.workspaceId}:${id}`" :project-id="id" /><p v-else>暂无工作空间。</p></template>
