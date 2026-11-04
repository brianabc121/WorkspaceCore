<script setup lang="ts">
// 任务时间轴（T-0105）：任务页“显示方式：时间轴”、项目页“时间轴”、任务详情“在时间轴中查看”都进到这里
import { useAuthStore } from '../stores/auth'
import TimelineBoard from '../features/timeline/TimelineBoard.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>任务时间轴</h1><p>每个执行者一条轨道：什么时候开始、还在不在执行、用了多少 token。</p></div><TimelineBoard v-if="auth.workspaceId" :key="`${auth.user?.id}:${auth.workspaceId}`" /><p v-else>暂无工作空间，请联系管理员。</p></template>
