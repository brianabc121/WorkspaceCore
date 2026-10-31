<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import DeveloperWorkspace from '../features/developer/DeveloperWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>开发者</h1><p>GitHub 仓库、Issues、Pull requests 与 Actions 的只读投影。</p></div><DeveloperWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" /><p v-else>暂无工作空间，请联系管理员。</p></template>
