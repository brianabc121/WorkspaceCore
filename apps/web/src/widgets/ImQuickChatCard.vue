<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../stores/auth'
import { dataOf } from '../services/api'
import { imError, useContacts, useImStatus } from '../features/im/queries'
import type { WidgetValue } from './registry'

// IM quick entry (replaces AIQuickAsk; V2 plan §2, contract §11): send one line to an OpenClaw instance and open
// the conversation. IM is for owner / admin only; the server enforces it, the card only explains.
const props = defineProps<{ value: WidgetValue }>()
const auth = useAuthStore(), router = useRouter(), cache = useQueryClient()
const scope = auth.workspaceId!, user = auth.user?.id
const status = useImStatus(scope), contacts = useContacts(scope)
const instances = computed(() => (contacts.data.value?.items ?? []).filter(c => c.instance_id && c.status !== 'disabled'))
const configured = computed(() => props.value.widget_type === 'im_quick_chat' ? props.value.config.instance_id ?? '' : '')
const target = ref(configured.value), text = ref(''), busy = ref(false), error = ref('')
watch(instances, list => { if (!target.value && list.length === 1) target.value = list[0]!.instance_id! }, { immediate: true })
// One Idempotency-Key per draft, so retrying after an unknown result cannot send twice.
let draftKey: string | null = null
watch(text, () => { draftKey = null })
async function send() {
  const message = text.value.trim()
  if (!message || !target.value || busy.value) return
  busy.value = true; error.value = ''
  draftKey ??= crypto.randomUUID()
  const header = { 'X-Workspace-Id': scope }
  try {
    const conversation = dataOf(await auth.api.POST('/api/v1/im/conversations', { params: { header }, body: { instance_id: target.value } }))
    dataOf(await auth.api.POST('/api/v1/im/conversations/{conversation_id}/messages', { params: { header: { ...header, 'Idempotency-Key': draftKey }, path: { conversation_id: conversation.conversation_id } }, body: { kind: 'text', text: message, at_im_ids: [] } }))
    if (auth.user?.id !== user || auth.workspaceId !== scope) return
    text.value = ''; draftKey = null
    await cache.invalidateQueries({ predicate: q => q.queryKey[0] === 'im' && q.queryKey[1] === user && q.queryKey[2] === scope })
    await router.push({ path: '/im', query: { c: conversation.conversation_id } })
  } catch (cause) { error.value = imError(cause) }
  finally { busy.value = false }
}
</script>
<template>
  <p v-if="!auth.canManage" class="card-state">IM 只对 owner 与 admin 开放。</p>
  <p v-else-if="status.isPending.value || contacts.isPending.value" role="status" class="card-state">正在连接 IM…</p>
  <p v-else-if="status.error.value" role="alert" class="card-state">{{ imError(status.error.value) }}</p>
  <p v-else-if="!status.data.value?.available" class="card-state">还没有部署 OpenIM，IM 暂不可用。</p>
  <p v-else-if="!instances.length" class="card-state">还没有可聊天的实例。<RouterLink to="/agents">登记 OpenClaw 实例 →</RouterLink></p>
  <form v-else class="im-group-form" aria-label="快捷提问" @submit.prevent="send">
    <label class="form-field"><span>实例</span><select v-model="target" :disabled="busy"><option value="" disabled>选择实例</option><option v-for="c in instances" :key="c.im_user_id" :value="c.instance_id!">{{ c.nickname }}（{{ c.status === 'online' ? '在线' : '离线' }}）</option></select></label>
    <label class="form-field"><span>问题</span><textarea v-model="text" rows="2" maxlength="4000" placeholder="发给实例的一句话，发送后打开会话" :disabled="busy" /></label>
    <p v-if="error" role="alert">{{ error }}</p>
    <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy || !target || !text.trim()">{{ busy ? '正在发送…' : '发送并打开会话' }}</button><RouterLink class="secondary-button" to="/im">打开 IM</RouterLink></div>
  </form>
</template>
