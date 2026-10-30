<script setup lang="ts">
import { ApiError, errorMessage } from '../services/api'
defineProps<{ loading: boolean; error?: unknown; empty: boolean; emptyText: string; stale?: boolean }>()
defineEmits<{ retry: [] }>()
</script>
<template>
  <p v-if="loading" role="status" class="data-state">正在加载…</p>
  <div v-else-if="error" class="data-state">
    <p role="alert">{{ error instanceof ApiError && error.status === 403 ? '你没有查看此内容的权限。' : errorMessage(error) }}</p>
    <p v-if="stale">当前显示的是上次获取的数据，可能已过期。</p>
    <button class="secondary-button" @click="$emit('retry')">重试</button>
  </div>
  <p v-else-if="empty" class="data-state">{{ emptyText }}</p>
</template>
