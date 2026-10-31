<script setup lang="ts">
import type { Schema } from '../services/api'
import type { WidgetValue } from './registry'
import { useAuthStore } from '../stores/auth'
import ScopedOperationsCard from './ScopedOperationsCard.vue'
defineProps<{ value: WidgetValue; overview?: Schema['DashboardOverview']; loading?: boolean; failed?: boolean }>()
defineEmits<{ retry: [] }>()
const auth = useAuthStore()
</script>
<template><ScopedOperationsCard v-if="auth.user && auth.workspaceId" :key="auth.user.id + ':' + auth.workspaceId" :value="value" :overview="overview" :loading="loading" :failed="failed" @retry="$emit('retry')" /></template>
