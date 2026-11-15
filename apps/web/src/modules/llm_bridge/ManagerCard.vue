<script setup lang="ts">
import { computed, toRef } from 'vue'
import { ACCOUNT_STATE, PLATFORM_SLOT, TARGET_KIND, bridgeError, networkName, platformName, type Bridge, type LlmBridgeApi, type ManagedAction, type Platform } from './api'

// 一个 MCP 管理器的卡片：名字、是否启用、它控制的平台和每个平台的账号是否已登录。
// 手动添加的写地址；工作台部署的（docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 7 节）写部署在哪台服务器、用哪种网络方式。
// 状态和下面详情用的是同一份查询结果，不会多请求一次。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge; selected: boolean; canWrite: boolean; busy: boolean }>()
const emit = defineEmits<{ select: []; edit: []; toggle: []; remove: []; operate: [action: ManagedAction] }>()
// 工作台部署的管理器：地址和令牌由部署管理，只能改名字；去掉它用“卸载”，不是“删除”。
// 部署它的服务器已经从工作台移除时，工作台登录不上去了：只能删除登记
const install = computed(() => props.bridge.deployment ?? null)
const removed = computed(() => !!install.value && !install.value.target_id)
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
    <p v-if="!install" class="asv-code">{{ bridge.base_url }}</p>
    <template v-else>
      <p class="form-help">部署在{{ TARGET_KIND[install.target_kind]?.name ?? '服务器' }}「{{ install.server_name }}」 · {{ networkName(install.mode, install.port) }}</p>
      <p v-if="removed" role="note" class="lb-warn">服务器已从工作台移除：工作台登录不上去了，只能删除登记；容器要到那台服务器上手动处理。</p>
      <p v-else-if="install.outdated" role="note" class="lb-warn">工作台带的版本比它新：可以“重新部署”来升级，登录状态和会话记录都保留。</p>
    </template>
    <p v-if="status.isPending.value" role="status" class="form-help">正在读取状态…</p>
    <p v-else-if="problem" role="alert" class="lb-warn is-red">{{ problem }}</p>
    <ul v-else class="lb-platforms" :aria-label="`${bridge.name}控制的平台`">
      <li v-for="p in platforms" :key="p.id"><span>{{ p.name }}</span><span class="asv-state" :class="p.tone">{{ p.text }}</span></li>
      <li v-if="!platforms.length" class="form-help">没有控制任何平台</li>
    </ul>
    <div v-if="canWrite" class="lb-inline">
      <button type="button" class="asv-link" :disabled="busy" :aria-label="`${install ? '改名' : '修改'}${bridge.name}`" @click="emit('edit')">{{ install ? '改名' : '修改' }}</button>
      <button type="button" class="asv-link" :disabled="busy" :aria-label="`${bridge.enabled ? '停用' : '启用'}${bridge.name}`" @click="emit('toggle')">{{ bridge.enabled ? '停用' : '启用' }}</button>
      <button v-if="!install" type="button" class="asv-link lb-danger" :disabled="busy" :aria-label="`删除${bridge.name}`" @click="emit('remove')">删除</button>
      <button v-else-if="removed" type="button" class="asv-link lb-danger" :disabled="busy" :aria-label="`卸载${bridge.name}（只删除登记）`" @click="emit('operate', 'uninstall')">卸载（只删除登记）</button>
      <template v-else>
        <button type="button" class="asv-link" :disabled="busy" :aria-label="`重新部署${bridge.name}`" @click="emit('operate', 'redeploy')">重新部署</button>
        <button type="button" class="asv-link" :disabled="busy" :aria-label="`重启${bridge.name}`" @click="emit('operate', 'restart')">重启</button>
        <button type="button" class="asv-link lb-danger" :disabled="busy" :aria-label="`卸载${bridge.name}`" @click="emit('operate', 'uninstall')">卸载</button>
      </template>
    </div>
  </article>
</template>
