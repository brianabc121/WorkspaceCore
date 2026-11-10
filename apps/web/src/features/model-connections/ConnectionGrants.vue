<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Bot } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'
import { useAgentAction } from '../agents/action'
import { STATUS } from '../agents/queries'
import { instanceIdsOf, isCode, mcError, useAllInstances, useGrants, type Connection } from './api'

// 智能体授权（契约第 2 节 GET / PUT grants）：勾选哪些 OpenClaw 实例可以用这条连接；保存是整体替换。
// 授权不等于使用：智能体的主模型在下方“OpenClaw 连接分配”里选。撤销正以它为主模型的实例返回 409 GRANT_IN_USE，先换绑定。
const props = defineProps<{ connection: Connection }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const grants = useGrants(scope, () => props.connection.id)
const instances = useAllInstances(scope)
const { busy, error, notice, failure, act, header } = useAgentAction(scope, mcError)
const chosen = ref<Set<string>>(new Set())
const granted = computed(() => new Set((grants.data.value ?? []).map(g => g.instance_id)))
const bound = computed(() => new Set((grants.data.value ?? []).filter(g => g.bound).map(g => g.instance_id)))
const dirty = computed(() => chosen.value.size !== granted.value.size || [...chosen.value].some(id => !granted.value.has(id)))
watch(() => grants.data.value, data => { if (data && !dirty.value) chosen.value = new Set(data.map(g => g.instance_id)) }, { immediate: true })
// 已授权、但实例列表里没有的（例如刚删）也列出来，免得整体替换时被悄悄撤销
const rows = computed(() => {
  const list = (instances.data.value ?? []).map(i => ({ id: i.id, name: i.name, status: i.status as keyof typeof STATUS | string }))
  for (const g of grants.data.value ?? []) if (!list.some(i => i.id === g.instance_id)) list.push({ id: g.instance_id, name: g.instance_name, status: g.instance_status })
  return list
})
const blocked = ref<string[]>([])
const nameOf = (id: string) => rows.value.find(r => r.id === id)?.name ?? id
function toggle(id: string, on: boolean) {
  const next = new Set(chosen.value)
  if (on) next.add(id); else next.delete(id)
  chosen.value = next
}
async function save() {
  const c = props.connection
  if (!dirty.value || busy.value || !grants.data.value) return
  blocked.value = []
  const ok = await act(async () => dataOf(await auth.api.PUT('/api/v1/model-connections/{connection_id}/grants', { params: { header, path: { connection_id: c.id } }, body: { version: c.version, instance_ids: [...chosen.value] } })), '授权已保存。')
  if (!ok && isCode(failure.value, 'GRANT_IN_USE')) blocked.value = instanceIdsOf(failure.value)
}
function reset() { chosen.value = new Set(granted.value); blocked.value = []; error.value = '' }
</script>

<template>
  <section class="mc-tabpanel" aria-label="智能体授权">
    <p class="form-help">勾选可以使用这条连接的 OpenClaw 智能体。授权后还要在“OpenClaw 连接分配”里把它设为主模型才会生效；撤销授权时同时撤销旧的密钥授权。</p>
    <p v-if="grants.isPending.value || instances.isPending.value" role="status" class="ws-state-line">正在读取授权…</p>
    <div v-else-if="grants.error.value || instances.error.value" class="ws-state-line"><p role="alert">{{ mcError(grants.error.value ?? instances.error.value) }}</p><button type="button" class="secondary-button" @click="grants.refetch(); instances.refetch()">重试</button></div>
    <p v-else-if="!rows.length" class="ws-state-line">还没有 OpenClaw 智能体。先到 <RouterLink to="/agents">OpenClaw 智能体</RouterLink> 部署或登记。</p>
    <form v-else class="mc-grants" aria-label="授权的智能体" @submit.prevent="save">
      <ul>
        <li v-for="r in rows" :key="r.id">
          <label>
            <input type="checkbox" :checked="chosen.has(r.id)" :disabled="busy" @change="toggle(r.id, ($event.target as HTMLInputElement).checked)" />
            <Bot aria-hidden="true" /><span class="mc-grant-name">{{ r.name }}</span>
            <span class="asv-sub">{{ STATUS[r.status as keyof typeof STATUS] ?? r.status }}<template v-if="bound.has(r.id)"> · 以它为主模型</template></span>
          </label>
        </li>
      </ul>
      <p v-if="[...bound].some(id => !chosen.has(id))" class="form-help" role="note">取消勾选的智能体正以它为主模型：保存会被拒绝，请先在“OpenClaw 连接分配”里更换。</p>
      <div v-if="blocked.length" class="mc-callout" role="alert">
        <p><strong>没有保存：这些智能体正以它为主模型</strong></p>
        <p>{{ blocked.map(nameOf).join('、') }}。请先在下方“OpenClaw 连接分配”里更换或解除它们的主模型，再撤销授权。</p>
      </div>
      <p v-else-if="error" class="feedback" role="alert">{{ error }}</p>
      <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
      <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy || !dirty">{{ busy ? '正在保存…' : '保存授权' }}</button><button type="button" class="secondary-button" :disabled="busy || !dirty" @click="reset">还原</button></div>
    </form>
  </section>
</template>
