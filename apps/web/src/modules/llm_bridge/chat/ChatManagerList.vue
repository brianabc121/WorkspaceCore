<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Plus, Search, Settings } from '@lucide/vue'
import type { Bridge, LlmBridgeApi } from '../api'
import ChatManagerItem from './ChatManagerItem.vue'

// 左栏：MCP 管理器（效果图第一栏）。每个管理器一张卡片：名字、进行中的会话数、连接状态（ChatManagerItem）。
// 上面可以按名字找；“+”（owner）和底部的“管理器设置”去“管理器与账号”。
// 只显示和发出事件；选中哪个由 LlmChatWorkspace 放在地址里（?m=）。
const props = defineProps<{ bridges: Bridge[]; managerId: string | null; api: LlmBridgeApi; canAdd: boolean }>()
const emit = defineEmits<{ select: [id: string] }>()

const q = ref('')
const shown = computed(() => {
  const term = q.value.trim().toLowerCase()
  return term ? props.bridges.filter(b => b.name.toLowerCase().includes(term)) : props.bridges
})
const settings = computed(() => ({ path: '/llm_bridge', query: { view: 'manage', ...(props.managerId ? { m: props.managerId } : {}) } }))
</script>

<template>
  <aside class="lc-col-card lc-managers" aria-label="MCP 管理器">
    <div class="lc-pane-head">
      <h2>MCP 管理器</h2>
      <RouterLink v-if="canAdd" class="lc-icon lc-head-add" :to="{ path: '/llm_bridge', query: { view: 'manage' } }" aria-label="添加 MCP 管理器" title="添加 MCP 管理器"><Plus aria-hidden="true" /></RouterLink>
    </div>
    <div class="lc-search">
      <Search aria-hidden="true" />
      <input v-model="q" type="search" maxlength="60" aria-label="搜索管理器" placeholder="搜索管理器" />
    </div>
    <ul class="lc-manager-list" aria-label="MCP 管理器列表">
      <ChatManagerItem v-for="b in shown" :key="b.id" :bridge="b" :api="api" :selected="b.id === managerId" @select="emit('select', b.id)" />
    </ul>
    <p v-if="!shown.length" class="lc-none">没有名字里带这几个字的管理器。</p>
    <RouterLink class="lc-manager-settings" :to="settings"><Settings aria-hidden="true" />管理器设置</RouterLink>
  </aside>
</template>
