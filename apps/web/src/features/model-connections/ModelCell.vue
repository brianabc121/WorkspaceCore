<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useModelPreview, type Connection } from './api'

// 表格“支持模型”一格：前几个明确的模型 ID（只读 3 个）+ 总数；“服务商全部可用模型”另加说明。没有模型写“待配置”。
const props = defineProps<{ connection: Connection }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const preview = useModelPreview(scope, () => props.connection.id, () => props.connection.model_count > 0)
const shown = computed(() => (preview.data.value?.items ?? []).slice(0, 3).map(m => m.model_id))
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
