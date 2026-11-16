<script setup lang="ts">
import { computed, toRef } from 'vue'
import { Link2, Monitor, Server } from '@lucide/vue'
import { PLATFORM_MARK } from './chat/chatApi'
import { ACCOUNT_STATE, PLATFORM_SLOT, bridgeError, platformName, type Bridge, type LlmBridgeApi, type Platform } from './api'

// 左边列表里的一个 MCP 管理器：名字、是否启用、连得上吗、在哪儿（本机 / 部署在哪台服务器 / 手动添加的地址）、
// 它控制的平台和每个平台的账号是否已登录（圆点颜色，读屏读出文字）。整张卡片点一下就选中它；
// 修改、停用、删除、重新部署等操作在右边详情的“管理操作”里。状态和详情用的是同一份查询结果，不会多请求一次。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge; selected: boolean; location: string; local?: boolean }>()
const emit = defineEmits<{ select: [] }>()
const status = props.api.useStatus(toRef(() => props.bridge.id))

const platforms = computed(() => {
  const data = status.data.value
  if (!data?.reachable) return []
  return data.platforms.map(id => {
    const account = data.accounts.find(a => a.slot === PLATFORM_SLOT[id as Platform])
    const state = account ? ACCOUNT_STATE[account.state] ?? { text: account.state, tone: 'tone-muted' } : { text: '没有报告', tone: 'tone-muted' }
    return { id, name: platformName(id), mark: PLATFORM_MARK[id] ?? '?', ...state }
  })
})
const problem = computed(() => {
  if (status.error.value) return bridgeError(status.error.value)
  const data = status.data.value
  return data && !data.reachable ? `连不上：${data.error || '没有给出原因'}` : ''
})
const connection = computed(() => (status.isPending.value ? { key: 'checking', text: '正在读取状态' } : problem.value ? { key: 'down', text: '连接异常' } : { key: 'ok', text: '已连接' }))
const icon = computed(() => (props.local ? Monitor : props.bridge.deployment ? Server : Link2))
</script>

<template>
  <article class="lb-manager" :class="{ 'is-selected': selected, 'is-off': !bridge.enabled }" :aria-label="`MCP 管理器：${bridge.name}`" :aria-current="selected ? 'true' : undefined">
    <div class="lb-manager-head">
      <span class="lb-manager-icon" aria-hidden="true"><component :is="icon" /></span>
      <h3><button type="button" class="lb-manager-pick" :aria-label="`查看 MCP 管理器：${bridge.name}`" @click="emit('select')">{{ bridge.name }}</button></h3>
      <span class="lb-badge" :class="bridge.enabled ? 'is-green' : 'is-muted'">{{ bridge.enabled ? '已启用' : '已停用' }}</span>
    </div>
    <p class="lb-manager-state">
      <span class="lb-dot" :data-state="connection.key" aria-hidden="true" />
      <span>{{ connection.text }}</span>
      <span class="lb-sep" aria-hidden="true">·</span>
      <span class="lb-manager-where">{{ location }}</span>
    </p>
    <p v-if="problem" role="alert" class="lb-manager-problem">{{ problem }}</p>
    <ul v-else-if="platforms.length" class="lb-chips" :aria-label="`${bridge.name}控制的平台`">
      <li v-for="p in platforms" :key="p.id" class="lb-chip" :class="p.tone" :title="`${p.name}：${p.text}`"><span class="lb-chip-mark" :data-platform="p.id" :data-mark="p.mark" aria-hidden="true" />{{ p.name }}<span class="lb-sr">{{ p.text }}</span></li>
    </ul>
    <p v-else-if="status.data.value?.reachable" class="lb-manager-none">没有控制任何平台</p>
  </article>
</template>
