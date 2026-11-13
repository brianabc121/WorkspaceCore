<script setup lang="ts">
import { computed, ref } from 'vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { PLATFORMS, bridgeError, type Grant, type GrantBody, type LlmBridgeApi, type Platform } from './api'

// 按实例的权限：哪个智能体能用大模型会话、能做到哪一步。每次改动立即保存（owner）。
// 关掉“启用”时其余权限一并收回（后端也这样处理）。
const props = defineProps<{ api: LlmBridgeApi; canWrite: boolean }>()
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

const bodyOf = (g: Grant): GrantBody => ({ enabled: g.enabled, can_write: g.can_write, can_account: g.can_account, can_takeover: g.can_takeover, platforms: g.platforms.filter((p): p is Platform => PLATFORMS.some(x => x.id === p)) })
async function save(g: Grant, body: GrantBody) {
  if (busy.value) return
  busy.value = g.instance_id; error.value = notice.value = ''
  try { await props.api.setGrant(g.instance_id, body); notice.value = `已保存「${g.instance_name}」的权限。` } catch (e) { error.value = bridgeError(e); redraw.value += 1; await grants.refetch() } finally { busy.value = '' }
}
function setEnabled(g: Grant, enabled: boolean) {
  return save(g, enabled ? { ...bodyOf(g), enabled: true } : { enabled: false, can_write: false, can_account: false, can_takeover: false, platforms: [] })
}
const setFlag = (g: Grant, flag: Flag, value: boolean) => save(g, { ...bodyOf(g), [flag]: value })
function setPlatform(g: Grant, platform: Platform, on: boolean) {
  const current = bodyOf(g).platforms
  return save(g, { ...bodyOf(g), platforms: PLATFORMS.map(p => p.id).filter(id => (id === platform ? on : current.includes(id))) })
}
const checked = (event: Event) => (event.target as HTMLInputElement).checked
</script>

<template>
  <ManagementSection title="实例权限" description="哪个智能体实例能用大模型会话，对所有登记的会话桥生效。默认都不启用。">
    <ul class="lb-help-list">
      <li><strong>启用</strong>：这个实例能看到并调用大模型会话的工具；关掉时下面几项一并收回。</li>
      <li v-for="f in FLAGS" :key="f.key"><strong>{{ f.name }}</strong>：{{ f.help }}</li>
      <li><strong>允许的平台</strong>：一个都不勾表示所有平台都允许。</li>
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
        </fieldset>
      </fieldset>
    </div>
  </ManagementSection>
</template>
