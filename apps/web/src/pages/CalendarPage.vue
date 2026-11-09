<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import CalendarWorkspace from '../features/calendar/CalendarWorkspace.vue'
const auth = useAuthStore()
</script>
<!-- 页头（标题、说明、新建日程）在 CalendarWorkspace 里，和工具条、三栏布局一起按效果图排版（T-0169） -->
<template><CalendarWorkspace v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" /><div v-else class="page-heading"><h1>日历</h1><p>暂无工作空间，请联系管理员。</p></div></template>
