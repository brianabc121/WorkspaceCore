<script setup lang="ts">
import DashboardWorkspace from '../features/dashboard/DashboardWorkspace.vue'
import { useAuthStore } from '../stores/auth'
import { computed } from 'vue'
const auth = useAuthStore()
// 问候语与今天的日期（T-0085；T-0164 按首页效果图：“晚上好，brian · 11月8日 星期日。”），按浏览器时间
const now = new Date(), hour = now.getHours()
const today = `${new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric' }).format(now)} ${new Intl.DateTimeFormat('zh-CN', { weekday: 'long' }).format(now)}`
const greeting = computed(() => `${hour < 6 ? '夜深了' : hour < 12 ? '上午好' : hour < 18 ? '下午好' : '晚上好'}${auth.user ? `，${auth.user.display_name}` : ''} · ${today}。`)
</script>
<template>
  <DashboardWorkspace v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" heading="工作总览" :greeting="greeting" />
  <template v-else>
    <div class="page-heading"><h1>工作总览</h1><p>{{ greeting }}</p></div>
    <section class="panel empty-state"><h2>暂无工作空间</h2><p>请联系管理员加入工作空间后使用仪表盘。</p></section>
  </template>
</template>
