<script setup lang="ts">
import { useAuthStore } from '../stores/auth'
import ImWorkspace from '../features/im/ImWorkspace.vue'
const auth = useAuthStore()
</script>
<template><div class="page-heading"><h1>消息</h1><p>与 OpenClaw 实例单聊、群聊。图片只显示工作台发出的，外部图片不自动加载。</p></div><ImWorkspace v-if="auth.workspaceId" :key="auth.user?.id + ':' + auth.workspaceId" /><p v-else>暂无工作空间，请联系管理员。</p></template>
