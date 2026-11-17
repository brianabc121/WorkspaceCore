<script setup lang="ts">
import { computed, toRef } from 'vue'
import { Server } from '@lucide/vue'
import type { Bridge, LlmBridgeApi } from '../api'

// 左栏里一个管理器的卡片：名字、进行中的会话数、连接状态。
// 连接状态读这个管理器的状态（查询键和中间一栏的一样，选中的那个不会重复请求）；停用的管理器不去读。
const props = defineProps<{ bridge: Bridge; api: LlmBridgeApi; selected: boolean }>()
defineEmits<{ select: [] }>()
const CONNECTION = { checking: '正在检查', ok: '已连接', down: '未连接', off: '已停用' } as const
const status = props.api.useStatus(toRef(() => (props.bridge.enabled ? props.bridge.id : null)))
const state = computed<keyof typeof CONNECTION>(() => {
  if (!props.bridge.enabled) return 'off'
  if (status.error.value) return 'down'
  const data = status.data.value
  return !data ? 'checking' : data.reachable ? 'ok' : 'down'
})
// 会话数来自会话索引（每分钟同步一次）；还没同步过时不写，不当成 0
const count = computed(() => (props.bridge.session_count == null ? '' : `${props.bridge.session_count} 个会话`))
</script>

<template>
  <li>
    <button type="button" class="lc-manager" :aria-current="selected ? 'true' : undefined" @click="$emit('select')">
      <span class="lc-manager-icon" aria-hidden="true"><Server /></span>
      <span class="lc-manager-main">
        <strong>{{ bridge.name }}</strong>
        <span v-if="count" class="lc-manager-count">{{ count }}</span>
      </span>
      <span class="lc-conn" :data-state="state">{{ CONNECTION[state] }}</span>
    </button>
  </li>
</template>
