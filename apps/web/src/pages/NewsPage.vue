<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import NewsWorkspace from '../features/news/NewsWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>早报</h1><p>每天的新闻早报、按平台的热榜、RSS 订阅、搜索与收藏。成员可以收藏或标“不感兴趣”，偏好会影响之后的早报。</p></div><NewsWorkspace v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" /><p v-else role="alert">请先选择一个工作空间。</p></template>
