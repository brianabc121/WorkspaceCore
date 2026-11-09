<script setup lang="ts">
import { CircleCheck, CircleDashed, CircleMinus, CircleX, TriangleAlert } from '@lucide/vue'
import { serviceLabels, type ServiceItem } from './display'
// 服务状态标签：图标 + 文字 + 颜色，不只靠颜色（T-0177）
defineProps<{ status: ServiceItem['status'] }>()
const ICONS = { ok: CircleCheck, degraded: TriangleAlert, down: CircleX, unknown: CircleDashed, not_configured: CircleMinus }
</script>
<template><span class="ws-state ops-state" :class="`is-${status}`"><component :is="ICONS[status]" aria-hidden="true" />{{ serviceLabels[status] }}</span></template>
