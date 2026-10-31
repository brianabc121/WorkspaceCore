<script setup lang="ts">
import { computed } from 'vue'
import { errorMessage, isAccessError, type Schema } from '../../services/api'
import { formatTime } from '../time'
const props = defineProps<{ loading: boolean; error?: unknown; denied?: boolean; sync?: Schema['SyncState']; asOf?: string | null; empty?: boolean; subject: 'services' | 'metrics'; stale?: boolean }>()
const stamp = computed(() => props.asOf ?? props.sync?.as_of ?? null)
const blocked = computed(() => props.denied || isAccessError(props.error) || props.sync?.status === 'unauthorized')
const expired = computed(() => !blocked.value && ((!!props.error || props.stale) && !!stamp.value || props.sync?.status === 'stale'))
</script>
<template><p v-if="blocked" role="alert" class="feedback">无权查看运维数据，请重新登录或联系管理员。</p><p v-else-if="loading" role="status">{{ subject === 'services' ? '正在加载服务状态…' : '正在加载指标…' }}</p><template v-else><p v-if="error" role="alert" class="feedback">{{ errorMessage(error) }}</p><p v-if="expired" class="feedback">数据可能过期（截至 <time v-if="stamp" :datetime="stamp">{{ formatTime(stamp) }}</time><span v-else>时间未知</span>），当前显示缓存。</p><p v-if="sync?.status === 'not_configured'" class="feedback">未部署监控。</p><p v-else-if="sync?.status === 'unavailable'" role="status" class="feedback">指标暂不可用，请稍后刷新。</p><p v-else-if="!expired && stamp" class="form-help">数据截至 <time :datetime="stamp">{{ formatTime(stamp) }}</time></p><p v-if="empty && !error && !expired && (!sync || sync.status === 'ok')">{{ subject === 'services' ? '暂无可显示的服务状态。' : '暂无指标数据。' }}</p></template></template>
