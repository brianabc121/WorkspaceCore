<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Plus } from '@lucide/vue'
import ManagementPage from '../../components/management/ManagementPage.vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import DataState from '../../components/DataState.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { isModuleDisabled } from '../../services/api'
import { bridgeError, useLlmBridge, type Bridge } from './api'
import BridgeAccounts from './BridgeAccounts.vue'
import BridgeSettingsForm from './BridgeSettingsForm.vue'
import BridgeEnvironments from './BridgeEnvironments.vue'
import BridgeSessions from './BridgeSessions.vue'
import BridgeGrants from './BridgeGrants.vue'
import './llm_bridge.css'

// 页面主体：登记的会话桥（owner 增删改），选中一座后看它的状态与登录、设置、开发环境、会话；最下面是按实例的权限。
const props = defineProps<{ scope: string }>()
const api = useLlmBridge(props.scope)
const canWrite = api.canWrite
const bridges = api.useBridges()
const list = computed(() => bridges.data.value ?? [])

const selectedId = ref<string | null>(null)
watch(list, items => { if (!items.some(b => b.id === selectedId.value)) selectedId.value = items[0]?.id ?? null }, { immediate: true })
const selected = computed(() => list.value.find(b => b.id === selectedId.value) ?? null)
const status = api.useStatus(selectedId)

const TABS = [{ key: 'accounts', name: '状态与登录' }, { key: 'settings', name: '设置' }, { key: 'environments', name: '开发环境' }, { key: 'sessions', name: '会话' }] as const
const tab = ref<(typeof TABS)[number]['key']>('accounts')

const error = ref(''), notice = ref(''), busy = ref(false)
const form = reactive({ open: false, id: null as string | null, name: '', base_url: '', token: '' })
function openForm(bridge?: Bridge) {
  Object.assign(form, { open: true, id: bridge?.id ?? null, name: bridge?.name ?? '', base_url: bridge?.base_url ?? '', token: '' })
  error.value = notice.value = ''
}
function closeForm() { Object.assign(form, { open: false, id: null, name: '', base_url: '', token: '' }) }
const formProblem = computed(() => {
  const name = form.name.trim(), url = form.base_url.trim()
  if (!name || name.length > 40) return '名字要 1–40 个字。'
  if (!/^https?:\/\/\S+$/.test(url) || url.length > 300) return '地址要以 http:// 或 https:// 开头，例如 http://10.0.0.5:8787。'
  if (form.token ? form.token.length < 32 || form.token.length > 200 : !form.id) return '令牌至少 32 个字符（部署会话桥时生成的管理令牌）。'
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
  return run(async () => {
    if (id) await api.updateBridge(id, { name, base_url, ...(token ? { token } : {}) })
    else selectedId.value = (await api.createBridge({ name, base_url, token })).id
    closeForm() // 令牌不留在页面上
    return id ? `已保存「${name}」。` : `已登记「${name}」。`
  })
}
function toggle(bridge: Bridge) {
  if (bridge.enabled && !window.confirm(`停用「${bridge.name}」？停用后智能体不能再经它发指令，登记和登录状态都保留。`)) return
  return run(async () => { await api.updateBridge(bridge.id, { enabled: !bridge.enabled }); return bridge.enabled ? `已停用「${bridge.name}」。` : `已启用「${bridge.name}」。` })
}
function remove(bridge: Bridge) {
  if (!window.confirm(`删除「${bridge.name}」的登记？只删除工作台里的登记，不影响会话桥容器本身和它里面的登录、会话。`)) return
  return run(async () => { await api.deleteBridge(bridge.id); if (form.id === bridge.id) closeForm(); return `已删除「${bridge.name}」的登记。` })
}
</script>

<template>
  <ManagementPage title="大模型会话桥" description="让智能体经会话桥向 ChatGPT / Codex / Claude / WorkBuddy 发指令并管理会话。会话桥是单独部署的容器。">
    <p v-if="isModuleDisabled(bridges.error.value)" role="status">功能已停用：大模型会话桥在当前工作空间没有启用。</p>
    <DataState v-else-if="bridges.isPending.value || bridges.error.value" :loading="bridges.isPending.value" :error="bridges.error.value" :empty="false" empty-text="" @retry="bridges.refetch()" />
    <template v-else>
      <p v-if="!canWrite" role="note" class="mg-hint">只有 owner 能修改大模型会话桥；你看到的是只读视图。</p>

      <ManagementSection title="会话桥" description="一座会话桥在每个平台上只登录一个账号。要管理第二个账号，就再部署一座会话桥，并在这里登记。">
        <template v-if="canWrite && !form.open" #actions>
          <button type="button" class="primary-button" @click="openForm()"><Plus aria-hidden="true" />添加会话桥</button>
        </template>
        <ActionFeedback :error="error" :notice="notice" />

        <div v-if="!list.length" class="lb-empty">
          <p><strong>还没有登记会话桥。</strong></p>
          <p>会话桥是一个单独部署的容器，里面装着各平台的官方程序，账号的登录也保存在它里面。</p>
          <p>一座会话桥在每个平台上只对应一个账号；要管理第二个账号，就再部署一座会话桥，并在这里登记。</p>
          <p>部署方法见部署指南的“大模型会话桥”一节；部署好后把它的地址和管理令牌填到这里。</p>
        </div>
        <table v-else class="asv-table lb-table">
          <thead><tr><th scope="col">名字</th><th scope="col">地址</th><th scope="col">状态</th><th scope="col">令牌</th><th v-if="canWrite" scope="col">操作</th></tr></thead>
          <tbody>
            <tr v-for="b in list" :key="b.id" :aria-selected="b.id === selectedId">
              <th scope="row"><button type="button" class="asv-link" :aria-label="`查看会话桥：${b.name}`" @click="selectedId = b.id">{{ b.name }}</button></th>
              <td data-label="地址" class="asv-code">{{ b.base_url }}</td>
              <td data-label="状态"><span class="asv-state" :class="b.enabled ? 'tone-green' : 'tone-muted'">{{ b.enabled ? '已启用' : '已停用' }}</span></td>
              <td data-label="令牌"><span class="asv-state" :class="b.has_token ? 'tone-green' : 'tone-amber'">{{ b.has_token ? '已登记令牌' : '没有令牌' }}</span></td>
              <td v-if="canWrite" data-label="操作" class="lb-row-actions">
                <button type="button" class="asv-link" :disabled="busy" :aria-label="`修改${b.name}`" @click="openForm(b)">修改</button>
                <button type="button" class="asv-link" :disabled="busy" :aria-label="`${b.enabled ? '停用' : '启用'}${b.name}`" @click="toggle(b)">{{ b.enabled ? '停用' : '启用' }}</button>
                <button type="button" class="asv-link lb-danger" :disabled="busy" :aria-label="`删除${b.name}`" @click="remove(b)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>

        <form v-if="canWrite && form.open" class="asv-form lb-form" :aria-label="form.id ? '修改会话桥' : '添加会话桥'" @submit.prevent="submit">
          <h3>{{ form.id ? '修改会话桥' : '添加会话桥' }}</h3>
          <div class="lb-grid">
            <div class="form-field"><label for="lb-bridge-name">名字</label><input id="lb-bridge-name" v-model="form.name" maxlength="40" required placeholder="例如：主账号" /><p class="form-help">智能体用这个名字指定走哪座会话桥。</p></div>
            <div class="form-field"><label for="lb-bridge-url">地址</label><input id="lb-bridge-url" v-model="form.base_url" maxlength="300" required inputmode="url" placeholder="http://10.0.0.5:8787" /></div>
          </div>
          <div class="form-field"><label for="lb-bridge-token">管理令牌</label>
            <input id="lb-bridge-token" v-model="form.token" type="password" autocomplete="new-password" maxlength="200" :required="!form.id" :placeholder="form.id ? '已登记（不回显）；不填表示不改' : '部署会话桥时生成的管理令牌，至少 32 个字符'" />
            <p class="form-help">令牌加密保存，页面上不再显示。</p>
          </div>
          <div class="lb-actions">
            <button type="button" class="secondary-button" :disabled="busy" @click="closeForm">取消</button>
            <button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在保存…' : form.id ? '保存' : '登记' }}</button>
          </div>
        </form>
      </ManagementSection>

      <section v-if="selected" class="lb-bridge" :aria-label="`会话桥：${selected.name}`">
        <div class="lb-tabs" role="tablist" :aria-label="`会话桥「${selected.name}」`">
          <button v-for="t in TABS" :key="t.key" type="button" role="tab" :aria-selected="tab === t.key" @click="tab = t.key">{{ t.name }}</button>
        </div>
        <div role="tabpanel" :aria-label="TABS.find(t => t.key === tab)?.name" class="lb-panel">
          <BridgeAccounts v-if="tab === 'accounts'" :key="selected.id" :api="api" :bridge="selected" :status="status.data.value" :loading="status.isPending.value" :error="status.error.value" :can-write="canWrite" @reload="status.refetch()" />
          <BridgeSettingsForm v-else-if="tab === 'settings'" :key="`s:${selected.id}`" :api="api" :bridge="selected" :status="status.data.value" :loading="status.isPending.value" :error="status.error.value" :can-write="canWrite" @reload="status.refetch()" />
          <BridgeEnvironments v-else-if="tab === 'environments'" :key="`e:${selected.id}`" :api="api" :bridge="selected" :can-write="canWrite" />
          <BridgeSessions v-else :key="`c:${selected.id}`" :api="api" :bridge="selected" />
        </div>
      </section>

      <BridgeGrants :api="api" :can-write="canWrite" />
    </template>
  </ManagementPage>
</template>
