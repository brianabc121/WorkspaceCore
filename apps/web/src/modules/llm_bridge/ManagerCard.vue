<script setup lang="ts">
import { computed, onBeforeUnmount, ref, toRef, watch } from 'vue'
import { Check, Link2, Monitor, MoreHorizontal, Server } from '@lucide/vue'
import { PLATFORM_MARK } from './chat/chatApi'
import { ACCOUNT_STATE, PLATFORM_SLOT, bridgeError, platformName, type Bridge, type LlmBridgeApi, type ManagedAction, type Platform } from './api'

// 左边列表里的一个 MCP 管理器（效果图“管理器与账号”）：
// - 第一行：名字、启用开关、“…”（管理操作）、选中时打勾；
// - 第二行：连得上吗 | 在哪儿（本机 / 部署在哪台服务器 / 手动添加的地址）；
// - 第三行：已配置平台，每个平台的账号是否已登录写在悬停提示里、读屏读出文字。
// 点卡片选中它，右边看详情。启用开关和“…”只给 owner；admin 看到的开关是灰的，只表示状态。
// “…”里：手动添加的是修改、删除；工作台部署的是改名、重新部署、重启、卸载（服务器已移除时只有“卸载（只删除登记）”）。
// 操作本身（确认、表单、进度）由 LlmBridgeWorkspace 做；状态和详情用的是同一份查询结果，不会多请求一次。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge; selected: boolean; location: string; local?: boolean; canWrite?: boolean; busy?: boolean }>()
const emit = defineEmits<{ select: []; toggle: []; edit: []; remove: []; operate: [action: ManagedAction] }>()
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
const install = computed(() => props.bridge.deployment ?? null)
const removed = computed(() => !!install.value && !install.value.target_id)

// “…”菜单：打开时才监听点外面和 Esc；做了一个操作后收起
const menuOpen = ref(false), menuBox = ref<HTMLElement | null>(null)
function outside(event: MouseEvent) { if (menuBox.value && !menuBox.value.contains(event.target as Node)) menuOpen.value = false }
function escape(event: KeyboardEvent) { if (event.key === 'Escape') menuOpen.value = false }
function listen(on: boolean) {
  if (on) {
    document.addEventListener('click', outside, true)
    document.addEventListener('keydown', escape)
  } else {
    document.removeEventListener('click', outside, true)
    document.removeEventListener('keydown', escape)
  }
}
watch(menuOpen, listen)
onBeforeUnmount(() => listen(false))
function pick(action: () => void) { menuOpen.value = false; action() }
</script>

<template>
  <article class="lb-manager" :class="{ 'is-selected': selected, 'is-off': !bridge.enabled, 'is-menu': menuOpen }" :aria-label="`MCP 管理器：${bridge.name}`" :aria-current="selected ? 'true' : undefined">
    <div class="lb-manager-head">
      <span class="lb-manager-icon" aria-hidden="true"><component :is="icon" /></span>
      <h3><button type="button" class="lb-manager-pick" :aria-label="`查看 MCP 管理器：${bridge.name}`" @click="emit('select')">{{ bridge.name }}</button></h3>
      <div class="lb-manager-tools">
        <button
          type="button" role="switch" class="lc-switch lb-manager-switch" :aria-checked="bridge.enabled" :aria-label="`启用${bridge.name}`"
          :title="canWrite ? (bridge.enabled ? '已启用：点一下停用' : '已停用：点一下启用') : bridge.enabled ? '已启用' : '已停用'"
          :disabled="!canWrite || busy" @click="emit('toggle')"
        >
          <span />
        </button>
        <div v-if="canWrite" ref="menuBox" class="lb-menu-wrap">
          <button type="button" class="lb-manager-more" :aria-label="`${bridge.name}的管理操作`" title="管理操作" aria-haspopup="true" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen"><MoreHorizontal aria-hidden="true" /></button>
          <div v-if="menuOpen" class="lb-menu" role="group" :aria-label="`管理操作：${bridge.name}`">
            <button type="button" :disabled="busy" :aria-label="`${install ? '改名' : '修改'}${bridge.name}`" @click="pick(() => emit('edit'))">{{ install ? '改名' : '修改' }}</button>
            <button v-if="!install" type="button" class="is-danger" :disabled="busy" :aria-label="`删除${bridge.name}`" @click="pick(() => emit('remove'))">删除</button>
            <button v-else-if="removed" type="button" class="is-danger" :disabled="busy" :aria-label="`卸载${bridge.name}（只删除登记）`" @click="pick(() => emit('operate', 'uninstall'))">卸载（只删除登记）</button>
            <template v-else>
              <button type="button" :disabled="busy" :aria-label="`重新部署${bridge.name}`" @click="pick(() => emit('operate', 'redeploy'))">重新部署</button>
              <button type="button" :disabled="busy" :aria-label="`重启${bridge.name}`" @click="pick(() => emit('operate', 'restart'))">重启</button>
              <button type="button" class="is-danger" :disabled="busy" :aria-label="`卸载${bridge.name}`" @click="pick(() => emit('operate', 'uninstall'))">卸载</button>
            </template>
          </div>
        </div>
        <Check v-if="selected" class="lb-manager-check" aria-hidden="true" />
      </div>
    </div>
    <p class="lb-manager-state">
      <span class="lb-dot" :data-state="connection.key" aria-hidden="true" />
      <span>{{ connection.text }}</span>
      <span class="lb-manager-bar" aria-hidden="true" />
      <component :is="icon" class="lb-manager-where-icon" aria-hidden="true" />
      <span class="lb-manager-where" :title="location">{{ location }}</span>
    </p>
    <p v-if="problem" role="alert" class="lb-manager-problem">{{ problem }}</p>
    <div v-else-if="platforms.length" class="lb-manager-platforms">
      <span class="lb-manager-label" aria-hidden="true">已配置平台：</span>
      <ul class="lb-chips" :aria-label="`${bridge.name}控制的平台`">
        <li v-for="p in platforms" :key="p.id" class="lb-chip" :class="p.tone" :title="`${p.name}：${p.text}`"><span class="lb-chip-mark" :data-platform="p.id" :data-mark="p.mark" aria-hidden="true" />{{ p.name }}<span class="lb-sr">{{ p.text }}</span></li>
      </ul>
    </div>
    <p v-else-if="status.data.value?.reachable" class="lb-manager-none">没有控制任何平台</p>
  </article>
</template>
