<script setup lang="ts">
import { computed, toRef } from 'vue'
import { ACCOUNT_STATE, PLATFORM_SLOT, bridgeError, platformName, type Bridge, type LlmBridgeApi, type Platform } from './api'

// 一个 MCP 管理器的卡片：名字、地址、是否启用、它控制的平台和每个平台的账号是否已登录。
// 状态和下面详情用的是同一份查询结果，不会多请求一次。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge; selected: boolean; canWrite: boolean; busy: boolean }>()
const emit = defineEmits<{ select: []; edit: []; toggle: []; remove: []; redeploy: []; uninstall: [] }>()
// 工作台一键部署的管理器（方案第 20 节）：能从页面上重新部署和卸载；部署它的服务器已经移除时只能删除登记
const managed = computed(() => !!props.bridge.deployment?.server_id)
const status = props.api.useStatus(toRef(() => props.bridge.id))

const platforms = computed(() => {
  const data = status.data.value
  if (!data?.reachable) return []
  return data.platforms.map(id => {
    const account = data.accounts.find(a => a.slot === PLATFORM_SLOT[id as Platform])
    const state = account ? ACCOUNT_STATE[account.state] ?? { text: account.state, tone: 'tone-muted' } : { text: '没有报告', tone: 'tone-muted' }
    return { id, name: platformName(id), ...state }
  })
})
const problem = computed(() => {
  if (status.error.value) return bridgeError(status.error.value)
  const data = status.data.value
  return data && !data.reachable ? `连不上：${data.error || '没有给出原因'}` : ''
})
</script>

<template>
  <article class="lb-manager" :class="{ 'is-selected': selected }" :aria-label="`MCP 管理器：${bridge.name}`" :aria-current="selected ? 'true' : undefined">
    <div class="lb-account-head">
      <h3><button type="button" class="asv-link" :aria-label="`查看 MCP 管理器：${bridge.name}`" @click="emit('select')">{{ bridge.name }}</button></h3>
      <span class="asv-state" :class="bridge.enabled ? 'tone-green' : 'tone-muted'">{{ bridge.enabled ? '已启用' : '已停用' }}</span>
    </div>
    <p class="asv-code">{{ bridge.base_url }}</p>
    <p v-if="bridge.deployment" class="form-help">工作台部署在服务器「{{ bridge.deployment.server_name }}」上{{ bridge.deployment.mode === 'same_host' ? '（和工作台同一台机器）' : '' }}</p>
    <p v-if="status.isPending.value" role="status" class="form-help">正在读取状态…</p>
    <p v-else-if="problem" role="alert" class="lb-warn is-red">{{ problem }}</p>
    <ul v-else class="lb-platforms" :aria-label="`${bridge.name}控制的平台`">
      <li v-for="p in platforms" :key="p.id"><span>{{ p.name }}</span><span class="asv-state" :class="p.tone">{{ p.text }}</span></li>
      <li v-if="!platforms.length" class="form-help">没有控制任何平台</li>
    </ul>
    <div v-if="canWrite" class="lb-inline">
      <button type="button" class="asv-link" :disabled="busy" :aria-label="`修改${bridge.name}`" @click="emit('edit')">修改</button>
      <button type="button" class="asv-link" :disabled="busy" :aria-label="`${bridge.enabled ? '停用' : '启用'}${bridge.name}`" @click="emit('toggle')">{{ bridge.enabled ? '停用' : '启用' }}</button>
      <button v-if="managed" type="button" class="asv-link" :disabled="busy" :aria-label="`重新部署${bridge.name}`" @click="emit('redeploy')">重新部署</button>
      <button v-if="managed" type="button" class="asv-link lb-danger" :disabled="busy" :aria-label="`卸载${bridge.name}`" @click="emit('uninstall')">卸载</button>
      <button v-else type="button" class="asv-link lb-danger" :disabled="busy" :aria-label="`删除${bridge.name}`" @click="emit('remove')">删除</button>
    </div>
  </article>
</template>
