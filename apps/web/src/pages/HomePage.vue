<script setup lang="ts">
import DashboardWorkspace from '../features/dashboard/DashboardWorkspace.vue'
import { useAuthStore } from '../stores/auth'
import { computed } from 'vue'
const auth = useAuthStore()
// 问候语与今天的日期（T-0085），按浏览器时间
const now = new Date(), hour = now.getHours()
const greeting = computed(() => `${hour < 6 ? '夜深了' : hour < 12 ? '上午好' : hour < 18 ? '下午好' : '晚上好'}${auth.user ? `，${auth.user.display_name}` : ''}`)
const today = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(now)
</script>
<template>
  <div class="page-heading"><h1>总览</h1><p>{{ greeting }}。今天是 {{ today }}。</p></div>
  <DashboardWorkspace v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" />
  <section v-else class="panel empty-state"><h2>暂无工作空间</h2><p>请联系管理员加入工作空间后使用仪表盘。</p></section>
</template>
