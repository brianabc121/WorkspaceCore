<script setup lang="ts">
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { serviceLabels, serviceReason } from './display'
defineProps<{ items: Schema['ServiceStatusOut'][]; compact?: boolean }>()
</script>
<template><div class="service-grid"><section v-for="item in items" :key="item.id" class="service-record" :data-state="item.status" role="region" :aria-label="item.name"><div class="section-heading"><component :is="compact ? 'h3' : 'h2'">{{ item.name }}</component><span class="service-state">{{ serviceLabels[item.status] }}</span></div><p class="form-help">{{ ({ core: '核心服务', monitoring: '监控组件', integration: '外部集成', agents: 'OpenClaw 与 IM' })[item.kind] }}</p><p v-if="serviceReason(item)">{{ serviceReason(item) }}</p><p class="form-help">最近检查：<time v-if="item.checked_at" :datetime="item.checked_at">{{ formatTime(item.checked_at) }}</time><span v-else>暂无记录</span></p><p v-if="item.latency_ms !== undefined && item.latency_ms !== null" class="form-help">检查耗时：{{ item.latency_ms }} ms</p></section></div></template>
