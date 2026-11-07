<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import NewsSettings from '../features/news/NewsSettings.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>新闻早报</h1><p>推送时间、早报方式、新闻来源、兴趣主题、大模型与推送渠道（owner 修改，admin 只读），以及 TrendRadar 的状态和学到的偏好。</p></div><NewsSettings v-if="auth.canManage" :key="`${auth.user?.id}:${auth.workspaceId}`" /><p v-else role="alert">只有 owner / admin 能查看新闻早报的设置。</p></template>
