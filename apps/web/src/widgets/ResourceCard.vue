<script setup lang="ts">
import type { Schema } from '../services/api'
import type { WidgetValue } from './registry'
import { useAuthStore } from '../stores/auth'
import ScopedResourceCard from './ScopedResourceCard.vue'
defineProps<{ value: WidgetValue; overview?: Schema['DashboardOverview']; loading?: boolean; failed?: boolean }>()
defineEmits<{ retry: [] }>()
const auth = useAuthStore()
</script>
<template><ScopedResourceCard v-if="auth.workspaceId && auth.user" :key="`${auth.user.id}:${auth.workspaceId}`" :value="value" :overview="overview" :loading="loading" :failed="failed" @retry="$emit('retry')" /><p v-else class="card-state">暂无可用工作空间。</p></template>
