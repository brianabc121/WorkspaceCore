<script setup lang="ts">
import { computed, ref } from 'vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import GrantEnvironments from './GrantEnvironments.vue'
import { PLATFORMS, bridgeError, type Bridge, type Grant, type GrantBody, type LlmBridgeApi } from './api'

// 按实例的权限：哪个智能体能用大模型会话、能用哪些 MCP 管理器、能做到哪一步。每次改动立即保存（owner）。
// 关掉“启用”时其余权限一并收回（后端也这样处理）。
// 允许的 MCP 管理器：bridge_ids 为 null 表示全部（包括以后添加的）；列表为空表示一个都不能用。
const props = defineProps<{ api: LlmBridgeApi; bridges: Bridge[]; canWrite: boolean }>()
const grants = props.api.useGrants()
const list = computed(() => grants.data.value ?? [])
const error = ref(''), notice = ref(''), busy = ref('')
// 保存失败时重画这一行：勾选框回到服务器上的状态，不停在刚点的样子
const redraw = ref(0)

const FLAGS = [
  { key: 'can_write', name: '新建会话和发送', help: '能新建会话、向会话发指令；不开只能看自己的会话。' },
  { key: 'can_account', name: '查看账号下全部会话', help: '能看到这个账号下的全部会话，包括别的实例建的和官方程序里已有的。' },
  { key: 'can_takeover', name: '接手别人的会话', help: '能向不是自己建的会话继续发指令。' },
] as const
type Flag = (typeof FLAGS)[number]['key']

// 允许的平台：一项是平台，或 codex:chat（只能和 Codex 对话，不能用开发模式；原来只勾了 ChatGPT 的实例是这一项，T-0280）
type GrantPlatform = GrantBody['platforms'][number]
const CODEX_CHAT: GrantPlatform = 'codex:chat'
const GRANT_IDS: GrantPlatform[] = [...PLATFORMS.map(p => p.id as GrantPlatform), CODEX_CHAT]
const bodyOf = (g: Grant): GrantBody => ({ enabled: g.enabled, can_write: g.can_write, can_account: g.can_account, can_takeover: g.can_takeover, platforms: g.platforms.filter((p): p is GrantPlatform => (GRANT_IDS as string[]).includes(p)), bridge_ids: g.bridge_ids ?? null, environment_ids: [...g.environment_ids] })
async function save(g: Grant, body: GrantBody) {
  if (busy.value) return
  busy.value = g.instance_id; error.value = notice.value = ''
  try { await props.api.setGrant(g.instance_id, body); notice.value = `已保存「${g.instance_name}」的权限。` } catch (e) { error.value = bridgeError(e); redraw.value += 1; await grants.refetch() } finally { busy.value = '' }
}
function setEnabled(g: Grant, enabled: boolean) {
  // 关掉时三项权限一并收回；已选的平台保留，再打开时不用重选
  return save(g, enabled ? { ...bodyOf(g), enabled: true } : { ...bodyOf(g), enabled: false, can_write: false, can_account: false, can_takeover: false })
}
const setFlag = (g: Grant, flag: Flag, value: boolean) => save(g, { ...bodyOf(g), [flag]: value })
function setPlatform(g: Grant, platform: GrantPlatform, on: boolean) {
  const current = bodyOf(g).platforms
  // 整个 Codex 和“只能对话”二选一
  const other = platform === 'codex' ? CODEX_CHAT : platform === CODEX_CHAT ? 'codex' : null
  return save(g, { ...bodyOf(g), platforms: GRANT_IDS.filter(id => (id === platform ? on : current.includes(id) && !(on && id === other))) })
}
// “全部”取消勾选时改成现在这几个（效果一样，但以后新添加的不会自动算进来）
const setAllBridges = (g: Grant, all: boolean) => save(g, { ...bodyOf(g), bridge_ids: all ? null : props.bridges.map(b => b.id) })
function setBridge(g: Grant, id: string, on: boolean) {
  const current = g.bridge_ids ?? []
  return save(g, { ...bodyOf(g), bridge_ids: props.bridges.map(b => b.id).filter(b => (b === id ? on : current.includes(b))) })
}
// 开发环境：一个都不勾 = 这个实例不能新建开发会话；没有“新建会话和发送”时用不上，后端会一并收回
const setEnvironment = (g: Grant, id: string, on: boolean) => save(g, { ...bodyOf(g), environment_ids: on ? [...new Set([...g.environment_ids, id])] : g.environment_ids.filter(e => e !== id) })
const usable = (g: Grant) => props.bridges.filter(b => g.bridge_ids == null || g.bridge_ids.includes(b.id))
const checked = (event: Event) => (event.target as HTMLInputElement).checked
</script>

<template>
  <ManagementSection title="实例权限" description="哪个智能体实例能用大模型会话、能用哪些 MCP 管理器。默认都不启用。">
    <ul class="lb-help-list">
      <li><strong>启用</strong>：这个实例能看到并调用大模型会话的工具；关掉时下面几项一并收回。</li>
      <li v-for="f in FLAGS" :key="f.key"><strong>{{ f.name }}</strong>：{{ f.help }}</li>
      <li><strong>允许的平台</strong>：一个都不勾表示所有平台都允许。</li>
      <li><strong>允许的 MCP 管理器</strong>：“全部”包括以后添加的；取消后只能用勾选的那几个。</li>
      <li><strong>可用的开发环境</strong>：只有勾选的开发环境，这个实例才能拿来新建开发会话；一个都不勾就不能新建开发会话（对话不受影响）。</li>
    </ul>
    <ActionFeedback :error="error" :notice="notice" />
    <p v-if="grants.isPending.value" role="status" class="data-state">正在读取实例权限…</p>
    <div v-else-if="grants.error.value" class="data-state">
      <p role="alert">{{ bridgeError(grants.error.value) }}</p>
      <button type="button" class="secondary-button" @click="grants.refetch()">重试</button>
    </div>
    <p v-else-if="!list.length" class="ws-state-line">这个工作空间还没有智能体实例。</p>
    <div v-else>
      <fieldset v-for="g in list" :key="`${g.instance_id}:${redraw}`" class="lb-checks lb-grant" :disabled="!canWrite || !!busy" :aria-label="`实例权限：${g.instance_name}`">
        <div class="lb-account-head"><h3>{{ g.instance_name }}</h3><span class="asv-state" :class="g.enabled ? 'tone-green' : 'tone-muted'">{{ g.enabled ? '已启用' : '未启用' }}</span></div>
        <div class="lb-inline">
          <label class="check-field"><input type="checkbox" :checked="g.enabled" @change="setEnabled(g, checked($event))" />启用</label>
          <label v-for="f in FLAGS" :key="f.key" class="check-field"><input type="checkbox" :checked="g.enabled && g[f.key]" :disabled="!g.enabled" @change="setFlag(g, f.key, checked($event))" />{{ f.name }}</label>
        </div>
        <fieldset class="lb-checks" :disabled="!g.enabled" :aria-label="`${g.instance_name}允许的平台`">
          <legend>允许的平台{{ g.enabled && !g.platforms.length ? '（现在：全部）' : '' }}</legend>
          <label v-for="p in PLATFORMS" :key="p.id" class="check-field"><input type="checkbox" :checked="g.platforms.includes(p.id)" @change="setPlatform(g, p.id, checked($event))" />{{ p.name }}</label>
          <label class="check-field"><input type="checkbox" :checked="g.platforms.includes(CODEX_CHAT)" @change="setPlatform(g, CODEX_CHAT, checked($event))" />Codex（只能对话）</label>
          <p class="form-help">“Codex（只能对话）”：能和 Codex 聊天，不能用开发模式，也读不到别人的开发会话。原来只勾了 ChatGPT 的实例升级后是这一项。</p>
        </fieldset>
        <fieldset v-if="bridges.length" class="lb-checks" :disabled="!g.enabled" :aria-label="`${g.instance_name}允许的 MCP 管理器`">
          <legend>允许的 MCP 管理器</legend>
          <label class="check-field"><input type="checkbox" :checked="g.bridge_ids == null" @change="setAllBridges(g, checked($event))" />全部（包括以后添加的）</label>
          <label v-for="b in bridges" :key="b.id" class="check-field"><input type="checkbox" :disabled="g.bridge_ids == null" :checked="g.bridge_ids == null || g.bridge_ids.includes(b.id)" @change="setBridge(g, b.id, checked($event))" />{{ b.name }}</label>
        </fieldset>
        <fieldset v-if="usable(g).length" class="lb-checks" :disabled="!g.enabled || !g.can_write" :aria-label="`${g.instance_name}可用的开发环境`">
          <legend>可用的开发环境{{ g.enabled && g.can_write && !g.environment_ids.length ? '（现在：一个都不能用）' : '' }}</legend>
          <GrantEnvironments v-for="b in usable(g)" :key="b.id" :api="api" :bridge="b" :grant="g" @toggle="(id, on) => setEnvironment(g, id, on)" />
        </fieldset>
        <p v-if="g.enabled && g.bridge_ids && !g.bridge_ids.length" role="note" class="lb-warn">这个实例现在一个 MCP 管理器都不能用：勾选至少一个，或者勾“全部”。</p>
      </fieldset>
    </div>
  </ManagementSection>
</template>
