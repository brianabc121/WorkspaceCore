<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import CalendarWorkspace from '../features/calendar/CalendarWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>日历</h1><p>工作空间共享日程，支持全天事件与时区显示。重复日程请逐次创建。</p></div><CalendarWorkspace v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" /><p v-else>暂无工作空间，请联系管理员。</p></template>
