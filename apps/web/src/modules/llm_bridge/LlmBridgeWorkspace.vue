<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Plus } from '@lucide/vue'
import ManagementPage from '../../components/management/ManagementPage.vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import DataState from '../../components/DataState.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { isModuleDisabled } from '../../services/api'
import { PLATFORMS, bridgeError, useLlmBridge, type Bridge, type Platform } from './api'
import ManagerCard from './ManagerCard.vue'
import BridgeAccounts from './BridgeAccounts.vue'
import BridgeSettingsForm from './BridgeSettingsForm.vue'
import BridgeEnvironments from './BridgeEnvironments.vue'
import BridgeSessions from './BridgeSessions.vue'
import BridgeGrants from './BridgeGrants.vue'
import './llm_bridge.css'

// 页面主体：MCP 管理器的列表（owner 增删改），选中一个后看它控制的平台与账号、设置、开发环境、会话；
// 最下面是按实例的权限。一个 MCP 管理器就是一座登记的会话桥：单独部署的容器，单独维护一套账号。
const props = defineProps<{ scope: string }>()
const api = useLlmBridge(props.scope)
const canWrite = api.canWrite
const bridges = api.useBridges()
const list = computed(() => bridges.data.value ?? [])

const selectedId = ref<string | null>(null)
watch(list, items => { if (!items.some(b => b.id === selectedId.value)) selectedId.value = items[0]?.id ?? null }, { immediate: true })
const selected = computed(() => list.value.find(b => b.id === selectedId.value) ?? null)
const status = api.useStatus(selectedId)

const TABS = [{ key: 'accounts', name: '平台与账号' }, { key: 'settings', name: '设置' }, { key: 'environments', name: '开发环境' }, { key: 'sessions', name: '会话' }] as const
const tab = ref<(typeof TABS)[number]['key']>('accounts')

const ALL = PLATFORMS.map(p => p.id)
const error = ref(''), notice = ref(''), busy = ref(false)
const form = reactive({ open: false, id: null as string | null, name: '', base_url: '', token: '', platforms: [...ALL] as Platform[] })
function openForm(bridge?: Bridge) {
  Object.assign(form, { open: true, id: bridge?.id ?? null, name: bridge?.name ?? '', base_url: bridge?.base_url ?? '', token: '', platforms: [...ALL] })
  error.value = notice.value = ''
}
function closeForm() { Object.assign(form, { open: false, id: null, name: '', base_url: '', token: '', platforms: [...ALL] }) }
const formProblem = computed(() => {
  const name = form.name.trim(), url = form.base_url.trim()
  if (!name || name.length > 40) return '名字要 1–40 个字。'
  if (!/^https?:\/\/\S+$/.test(url) || url.length > 300) return '地址要以 http:// 或 https:// 开头，例如 http://llm-bridge:8787。'
  if (form.token ? form.token.length < 32 || form.token.length > 200 : !form.id) return '令牌至少 32 个字符（部署这个 MCP 管理器时生成的管理令牌）。'
  if (!form.id && !form.platforms.length) return '至少勾选一个要控制的平台。'
  return ''
})
async function run(action: () => Promise<string>) {
  if (busy.value) return
  busy.value = true; error.value = notice.value = ''
  try { notice.value = await action() } catch (e) { error.value = bridgeError(e) } finally { busy.value = false }
}
function submit() {
  if (formProblem.value) { error.value = formProblem.value; return }
  const name = form.name.trim(), base_url = form.base_url.trim(), token = form.token, id = form.id
  // 按固定顺序提交，和页面上的顺序一致
  const platforms = ALL.filter(p => form.platforms.includes(p))
  return run(async () => {
    if (id) await api.updateBridge(id, { name, base_url, ...(token ? { token } : {}) })
    else selectedId.value = (await api.createBridge({ name, base_url, token, platforms })).id
    closeForm() // 令牌不留在页面上
    if (!id) tab.value = 'accounts'
    return id ? `已保存「${name}」。` : `已添加「${name}」。接下来在下面的“平台与账号”里登录它控制的平台。`
  })
}
function toggle(bridge: Bridge) {
  if (bridge.enabled && !window.confirm(`停用「${bridge.name}」？停用后智能体不能再经它发指令，它的登录状态和会话记录都保留。`)) return
  return run(async () => { await api.updateBridge(bridge.id, { enabled: !bridge.enabled }); return bridge.enabled ? `已停用「${bridge.name}」。` : `已启用「${bridge.name}」。` })
}
function remove(bridge: Bridge) {
  if (!window.confirm(`删除「${bridge.name}」？\n\n只是从工作台里去掉它，不影响它的容器和里面的登录、会话。只允许用它的实例，之后一个 MCP 管理器都用不了，要到“实例权限”里重新选。`)) return
  return run(async () => { await api.deleteBridge(bridge.id); await api.invalidate('grants'); if (form.id === bridge.id) closeForm(); return `已删除「${bridge.name}」。` })
}
</script>

<template>
  <ManagementPage title="大模型客户端管理" description="添加一个或多个 MCP 管理器。每个管理器控制你勾选的平台（ChatGPT / Codex / Claude / WorkBuddy），单独维护一套账号；智能体经它向这些平台发指令、管理会话。">
    <p v-if="isModuleDisabled(bridges.error.value)" role="status">功能已停用：大模型客户端在当前工作空间没有启用。</p>
    <DataState v-else-if="bridges.isPending.value || bridges.error.value" :loading="bridges.isPending.value" :error="bridges.error.value" :empty="false" empty-text="" @retry="bridges.refetch()" />
    <template v-else>
      <p v-if="!canWrite" role="note" class="mg-hint">只有 owner 能修改大模型客户端；你看到的是只读视图。</p>

      <ManagementSection title="MCP 管理器" description="一个 MCP 管理器在每个平台上只登录一个账号。同一个平台要用第二个账号，就再添加一个 MCP 管理器。">
        <template v-if="canWrite && !form.open" #actions>
          <button type="button" class="primary-button" @click="openForm()"><Plus aria-hidden="true" />添加 MCP 管理器</button>
        </template>
        <ActionFeedback :error="error" :notice="notice" />

        <div v-if="!list.length" class="lb-empty">
          <p><strong>还没有 MCP 管理器。</strong></p>
          <p>MCP 管理器是一个单独部署的容器，里面装着各平台的官方程序；账号的登录保存在它里面，不保存在工作台。</p>
          <p>每个管理器控制你勾选的平台，在每个平台上只登录一个账号；可以添加多个，各自一套账号。</p>
          <p>{{ canWrite ? '点“添加 MCP 管理器”，按里面的步骤部署并填写地址和令牌。' : '请 owner 来添加。' }}</p>
        </div>
        <div v-else class="lb-managers" role="list" aria-label="MCP 管理器列表">
          <div v-for="b in list" :key="b.id" role="listitem">
            <ManagerCard :api="api" :bridge="b" :selected="b.id === selectedId" :can-write="canWrite" :busy="busy" @select="selectedId = b.id" @edit="openForm(b)" @toggle="toggle(b)" @remove="remove(b)" />
          </div>
        </div>

        <form v-if="canWrite && form.open" class="asv-form lb-form" novalidate :aria-label="form.id ? '修改 MCP 管理器' : '添加 MCP 管理器'" @submit.prevent="submit">
          <h3>{{ form.id ? '修改 MCP 管理器' : '添加 MCP 管理器' }}</h3>
          <details v-if="!form.id" class="lb-deploy">
            <summary>还没有部署？先在服务器上启动它</summary>
            <ol class="lb-steps">
              <li>第一个管理器：在工作台的服务器上运行 <span class="asv-code">make llm-bridge-up</span>。地址填 <span class="asv-code">http://llm-bridge:8787</span>，令牌用 <span class="asv-code">make llm-bridge-token</span> 查看。</li>
              <li>第 2 个、第 3 个……：运行 <span class="asv-code">make llm-bridge-up N=2</span>。地址填 <span class="asv-code">http://llm-bridge-2:8787</span>，令牌用 <span class="asv-code">make llm-bridge-token N=2</span> 查看（N 换成 3、4 同理）。</li>
              <li>部署在别的服务器上时，见部署指南的“大模型客户端”一节。</li>
            </ol>
          </details>
          <div class="lb-grid">
            <div class="form-field"><label for="lb-bridge-name">名字</label><input id="lb-bridge-name" v-model="form.name" maxlength="40" required placeholder="例如：1 号" /><p class="form-help">智能体用这个名字指定走哪个 MCP 管理器。</p></div>
            <div class="form-field"><label for="lb-bridge-url">地址</label><input id="lb-bridge-url" v-model="form.base_url" maxlength="300" required inputmode="url" placeholder="http://llm-bridge:8787" /><p class="form-help">工作台访问这个管理器的地址。</p></div>
          </div>
          <div class="form-field">
            <label for="lb-bridge-token">管理令牌</label>
            <input id="lb-bridge-token" v-model="form.token" type="password" autocomplete="new-password" maxlength="200" :required="!form.id" :placeholder="form.id ? '已保存（不回显）；不填表示不改' : '部署时生成的管理令牌，至少 32 个字符'" />
            <p class="form-help">令牌加密保存，页面上不再显示。</p>
          </div>
          <fieldset v-if="!form.id" class="lb-checks" aria-label="控制的平台">
            <legend>控制的平台</legend>
            <label v-for="p in PLATFORMS" :key="p.id" class="check-field"><input v-model="form.platforms" type="checkbox" :value="p.id" />{{ p.name }}</label>
          </fieldset>
          <p v-if="!form.id" class="form-help">只有勾选的平台能在这个管理器上登录账号、被智能体使用；之后可以在“平台与账号”里改。ChatGPT 和 Codex 用的是同一个 ChatGPT 账号。</p>
          <div class="lb-actions">
            <button type="button" class="secondary-button" :disabled="busy" @click="closeForm">取消</button>
            <button type="submit" class="primary-button" :disabled="busy">{{ busy ? (form.id ? '正在保存…' : '正在连接…') : form.id ? '保存' : '添加' }}</button>
          </div>
        </form>
      </ManagementSection>

      <section v-if="selected" class="lb-bridge" :aria-label="`MCP 管理器：${selected.name}`">
        <div class="lb-tabs" role="tablist" :aria-label="`MCP 管理器「${selected.name}」`">
          <button v-for="t in TABS" :key="t.key" type="button" role="tab" :aria-selected="tab === t.key" @click="tab = t.key">{{ t.name }}</button>
        </div>
        <div role="tabpanel" :aria-label="TABS.find(t => t.key === tab)?.name" class="lb-panel">
          <BridgeAccounts v-if="tab === 'accounts'" :key="selected.id" :api="api" :bridge="selected" :status="status.data.value" :loading="status.isPending.value" :load-error="status.error.value" :can-write="canWrite" @reload="status.refetch()" />
          <BridgeSettingsForm v-else-if="tab === 'settings'" :key="`s:${selected.id}`" :api="api" :bridge="selected" :status="status.data.value" :loading="status.isPending.value" :load-error="status.error.value" :can-write="canWrite" @reload="status.refetch()" />
          <BridgeEnvironments v-else-if="tab === 'environments'" :key="`e:${selected.id}`" :api="api" :bridge="selected" :status="status.data.value" :can-write="canWrite" />
          <BridgeSessions v-else :key="`c:${selected.id}`" :api="api" :bridge="selected" />
        </div>
      </section>

      <BridgeGrants :api="api" :bridges="list" :can-write="canWrite" />
    </template>
  </ManagementPage>
</template>
