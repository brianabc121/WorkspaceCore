<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import KnowledgeWorkspace from '../features/knowledge/KnowledgeWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>知识库</h1><p>收藏 AFFiNE 文档链接，关联项目与任务。工作台只保存书签元数据，不抓取文档正文。</p></div><KnowledgeWorkspace v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" /><p v-else>暂无工作空间，请联系管理员。</p></template>
