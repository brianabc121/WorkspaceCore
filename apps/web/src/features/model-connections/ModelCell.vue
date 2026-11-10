<script setup lang="ts">
import { computed } from 'vue'
import type { Connection } from './api'

// 表格“支持模型”一格：列表接口带回的前 3 个模型 ID（按 ID 排序）+ 剩余数；“服务商全部可用模型”另加说明。没有模型写“待配置”。
const props = defineProps<{ connection: Connection }>()
const shown = computed(() => props.connection.model_preview ?? [])
const more = computed(() => Math.max(0, props.connection.model_count - shown.value.length))
</script>

<template>
  <span v-if="!connection.model_count" class="mc-model-chip is-empty">待配置</span>
  <span v-else class="mc-model-cell">
    <span class="mc-model-chips">
      <code v-for="m in shown" :key="m" class="mc-model-chip">{{ m }}</code>
      <span v-if="!shown.length" class="asv-sub">{{ connection.model_count }} 个模型</span>
      <span v-else-if="more" class="mc-model-chip is-more">+{{ more }}</span>
    </span>
    <span v-if="connection.model_scope === 'all_available'" class="asv-sub">服务商全部可用模型</span>
  </span>
</template>
