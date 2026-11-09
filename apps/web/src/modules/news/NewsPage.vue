<script setup lang="ts">
import { useAuthStore } from '../../stores/auth'
import NewsWorkspace from './NewsWorkspace.vue'
import '../../features/workspace/workspace-pages.css'
// 早报页：页头在 NewsWorkspace 里（刷新、早报设置）；换用户或工作空间时整页重建，上个空间的早报不会留在页面上
const auth = useAuthStore()
</script>
<template>
  <NewsWorkspace v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" />
  <template v-else>
    <header class="ws-header"><div><h1>早报</h1><p>每天的新闻早报、按平台的热榜、RSS 订阅、搜索与收藏。</p></div></header>
    <p role="alert">请先选择一个工作空间。</p>
  </template>
</template>
