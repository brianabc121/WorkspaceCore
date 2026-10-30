<script setup lang="ts">
import type { Schema } from '../services/api'
import { formatTime } from '../features/time'
defineProps<{ sync?: Schema['SyncState'] }>()
</script>
<template>
  <p v-if="sync" class="feedback" role="status">{{ ({ ok: '数据已同步', stale: '数据可能已过期，当前显示缓存', unavailable: '任务服务暂不可用', unauthorized: 'Vikunja 授权已失效，请管理员更新连接', not_configured: '尚未配置可用的 Vikunja 连接' })[sync.status] }} · 最近同步：{{ formatTime(sync.as_of) }}</p>
</template>
