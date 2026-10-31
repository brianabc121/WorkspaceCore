<script setup lang="ts">
import { computed } from 'vue'
import type { Schema } from '../services/api'
import { formatTime } from '../features/time'
const props = withDefaults(defineProps<{ sync?: Schema['SyncState']; provider?: 'vikunja' | 'github' }>(), { provider: 'vikunja', sync: undefined })
const message = computed(() => {
  if (!props.sync) return ''
  const name = props.provider === 'github' ? 'GitHub' : 'Vikunja', s = props.sync
  if (s.status === 'unauthorized') return name + ' 授权已失效，请管理员重新连接'
  if (s.reason === 'rate_limited') return 'GitHub 限流中' + (s.retry_at ? '，预计 ' + formatTime(s.retry_at) + ' 恢复' : '，恢复时间待确认')
  return ({ ok: '数据已同步', stale: '数据可能已过期，当前显示缓存', unavailable: name + ' 暂不可用，请等待同步或稍后重试', unauthorized: '', not_configured: '尚未配置可用的 ' + name + ' 连接' })[s.status]
})
</script>
<template><p v-if="sync" class="feedback" role="status">{{ message }} · 最近同步：{{ formatTime(sync.as_of) }}</p></template>
