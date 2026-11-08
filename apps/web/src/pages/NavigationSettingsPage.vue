<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import NavigationSettings from '../features/navigation/NavigationSettings.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>侧栏与页面</h1><p>自定义侧栏分组和顺序，把页面挪到别的分组，添加外部链接、站内页面、知识库笔记、仪表盘和嵌入页（owner 修改，admin 只读）。</p></div><NavigationSettings v-if="auth.canManage" :key="`${auth.user?.id}:${auth.workspaceId}`" /><p v-else role="alert">只有 owner / admin 能查看侧栏设置。</p></template>
