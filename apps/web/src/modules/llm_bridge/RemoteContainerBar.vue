<script setup lang="ts">
import { computed, ref, toRef } from 'vue'
import { Server } from '@lucide/vue'
import { formatTime } from '../../features/time'
import RemoteLoginPanel from './RemoteLoginPanel.vue'
import {
  CONTAINER_STATE, IDLE_STOP, REMOTE_NETWORK, REMOTE_PLATFORM_STATE, bridgeError, resourceText, slotNames,
  type LlmBridgeApi, type RemoteAction, type RemoteContainer, type RemoteNetwork, type Slot,
} from './api'

// 开发环境页上远程环境的容器信息条（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 10.2）：状态、资源、最近使用、
// 在容器服务器查看；启动 / 停止 / 重试 / 更新镜像 / 删除容器（owner）；各平台在容器里能不能用，要登录的可以在这里登录。
const props = defineProps<{ api: LlmBridgeApi; bridgeId: string; containerId: string; container: RemoteContainer | null; label: string | null; canWrite: boolean }>()
const busy = ref(false), error = ref(''), notice = ref('')
const login = ref<{ slot: Slot; label: string } | null>(null)
const platforms = props.api.useContainerPlatforms(toRef(() => props.bridgeId), toRef(() => props.containerId))
const slots = computed(() => Object.entries(platforms.data.value ?? {}) as [Slot, NonNullable<typeof platforms.data.value>[string]][])
const c = computed(() => props.container)
const state = computed(() => (c.value ? CONTAINER_STATE[c.value.state] : null))
const runnerLink = computed(() => (c.value?.runner_environment_id ? `/runner?environment_id=${encodeURIComponent(c.value.runner_environment_id)}` : '/runner'))
const ACTION: Record<RemoteAction, string> = { start: '启动', stop: '停止', retry: '重试', update_image: '更新镜像' }
const actions = computed<RemoteAction[]>(() => {
  const x = c.value
  if (!x) return []
  const out: RemoteAction[] = []
  if (x.state === 'stopped' || x.state === 'archived') out.push('start')
  if (x.state === 'running') out.push('stop')
  if (x.state === 'failed') out.push('retry')
  if (!x.versions_ok && !x.updating_image && ['running', 'stopped'].includes(x.state)) out.push('update_image')
  return out
})

async function act(action: RemoteAction) {
  if (!c.value || busy.value) return
  if (action === 'stop' && !window.confirm(`停止远程容器「${c.value.name}」？正在跑的回合和后台作业会中断；数据保留，下次发消息时自动启动。`)) return
  if (action === 'update_image' && !window.confirm(`按 MCP 管理器现在的命令行版本更新「${c.value.name}」的镜像？先重新构建镜像（几分钟），再按新镜像重建容器；会话的工作目录和容器里的登录都保留。`)) return
  busy.value = true; error.value = notice.value = ''
  try { await props.api.containerAction(props.bridgeId, c.value, action); notice.value = `已发出“${ACTION[action]}”：${c.value.name}（以刷新后的状态为准）` } catch (e) { error.value = bridgeError(e) } finally { busy.value = false }
}
// 设置（PATCH）：空闲停止、网络、另加的域名。网络在下一次启动或执行时生效；资源建好后不能改
const editing = ref(false)
const form = ref({ network: 'all' as RemoteNetwork, domains: '', idle: 30 as 0 | 30 | 120 })
const NETWORKS = Object.entries(REMOTE_NETWORK) as [RemoteNetwork, { name: string; help: string }][]
function openSettings() {
  const x = c.value
  if (!x) return
  form.value = { network: x.network, domains: x.domains.join('\n'), idle: ([0, 30, 120].includes(x.idle_stop_minutes) ? x.idle_stop_minutes : 30) as 0 | 30 | 120 }
  editing.value = true; error.value = notice.value = ''
}
async function saveSettings() {
  const x = c.value
  if (!x || busy.value) return
  const domains = form.value.domains.split(/[\s,，]+/).map(d => d.trim().toLowerCase()).filter(Boolean)
  if (domains.length > 50) { error.value = '另加的域名最多 50 个。'; return }
  busy.value = true; error.value = notice.value = ''
  try {
    await props.api.updateContainer(props.bridgeId, x.id, { version: x.version, network: form.value.network, domains, idle_stop_minutes: form.value.idle })
    editing.value = false; notice.value = `已保存「${x.name}」的设置（网络在下一次启动或执行时生效）。`
  } catch (e) { error.value = bridgeError(e) } finally { busy.value = false }
}
async function remove() {
  if (!c.value || busy.value) return
  if (!window.confirm(`删除远程容器「${c.value.name}」？容器和数据卷一起删、不做快照：会话的工作目录和容器里的登录都会没了，不能恢复。`)) return
  busy.value = true; error.value = notice.value = ''
  try { await props.api.deleteContainer(props.bridgeId, c.value.id); notice.value = `已删除远程容器「${c.value.name}」。` } catch (e) { error.value = bridgeError(e) } finally { busy.value = false }
}
</script>

<template>
  <section class="lb-remote-bar" :aria-label="`远程容器：${container?.name ?? label ?? containerId}`">
    <div class="lb-remote-head">
      <Server aria-hidden="true" />
      <div class="lb-remote-text">
        <strong>{{ container?.name ?? label ?? '远程容器' }}</strong>
        <span v-if="container">
          <span class="asv-state" :class="state?.tone">{{ state?.text }}</span>
          · {{ container.host_name ?? '—' }} · {{ resourceText(container) }}
          <template v-if="container.last_used_at"> · 最近使用 {{ formatTime(container.last_used_at) }}</template>
        </span>
        <span v-else class="form-help">读不到这个远程容器（可能已经删除）。</span>
      </div>
      <RouterLink v-if="container" class="asv-link" :to="runnerLink">在容器服务器查看 →</RouterLink>
    </div>
    <p v-if="container?.state_reason && ['building', 'creating', 'failed', 'gone', 'deleting'].includes(container.state)" class="form-help lb-remote-reason">{{ container.state_reason }}</p>
    <p v-if="container && !container.versions_ok" role="note" class="lb-warn">容器里的命令行版本和 MCP 管理器不一致：{{ container.updating_image ? '正在更新镜像…' : '点“更新镜像”，不更新就不能开回合。' }}</p>
    <p v-if="container?.image_error" role="alert" class="lb-warn is-red">更新镜像没成：{{ container.image_error }}</p>
    <div v-if="canWrite && container" class="lb-actions">
      <button v-for="a in actions" :key="a" type="button" class="secondary-button" :disabled="busy" @click="act(a)">{{ ACTION[a] }}</button>
      <button type="button" class="secondary-button" :disabled="busy" :aria-expanded="editing" @click="editing ? (editing = false) : openSettings()">设置</button>
      <button type="button" class="secondary-button lb-danger" :disabled="busy" @click="remove">删除容器</button>
    </div>
    <form v-if="editing && container" class="lb-remote-settings" aria-label="远程容器的设置" @submit.prevent="saveSettings">
      <div class="form-field">
        <label for="lb-remote-network">网络</label>
        <select id="lb-remote-network" v-model="form.network"><option v-for="[id, n] in NETWORKS" :key="id" :value="id">{{ n.name }}</option></select>
        <p class="form-help">{{ REMOTE_NETWORK[form.network].help }}</p>
      </div>
      <div v-if="form.network !== 'all'" class="form-field">
        <label for="lb-remote-domains">另加的域名</label>
        <textarea id="lb-remote-domains" v-model="form.domains" rows="2" placeholder="每行一个，例如 *.example.com" />
      </div>
      <div class="form-field">
        <label for="lb-remote-idle">空闲停止</label>
        <select id="lb-remote-idle" v-model.number="form.idle"><option v-for="o in IDLE_STOP" :key="o.minutes" :value="o.minutes">{{ o.name }}</option></select>
      </div>
      <p class="form-help">资源（CPU、内存、磁盘）建好后不能改：要更大的就新建一个远程容器。</p>
      <div class="lb-actions">
        <button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在保存…' : '保存设置' }}</button>
        <button type="button" class="secondary-button" :disabled="busy" @click="editing = false">取消</button>
      </div>
    </form>
    <p v-if="error" role="alert" class="lb-warn is-red">{{ error }}</p>
    <p v-if="notice" role="status" class="form-help">{{ notice }}</p>
    <ul v-if="slots.length" class="lb-remote-platforms" aria-label="各平台在容器里">
      <li v-for="[slot, p] in slots" :key="slot">
        <span>{{ slotNames([slot])[0] }}</span>
        <span class="asv-state" :class="REMOTE_PLATFORM_STATE[p.state].tone">{{ REMOTE_PLATFORM_STATE[p.state].text }}</span>
        <span v-if="p.detail" class="form-help">{{ p.detail }}</span>
        <button v-if="canWrite && p.can_login && (p.state === 'needs_login' || p.state === 'mismatch')" type="button" class="secondary-button" @click="login = { slot, label: slotNames([slot])[0]! }">在容器里登录</button>
      </li>
    </ul>
    <RemoteLoginPanel v-if="login && container" :api="api" :bridge-id="bridgeId" :container-id="container.id" :account="login.slot" :label="login.label" :container-name="container.name" @close="login = null" />
  </section>
</template>
