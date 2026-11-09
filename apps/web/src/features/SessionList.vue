<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../stores/auth'
import { dataOf, isAccessError, type Schema } from '../services/api'
import { useAction } from './forms'
import DataState from '../components/DataState.vue'
import ActionFeedback from '../components/ActionFeedback.vue'
import { Laptop, RefreshCw } from '@lucide/vue'
import SessionRow from './security/SessionRow.vue'
const auth = useAuthStore()
// 账户级：查询键按用户，不跟着工作空间变；只能撤销自己的会话（服务端按当前用户查）
const query = useQuery({ queryKey: computed(() => ['sessions', auth.user?.id]), queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/auth/sessions', { signal })), enabled: computed(() => !!auth.user) })
const selected = ref<Schema['SessionOut'] | null>(null)
const counts = computed(() => { const items = query.data.value?.items ?? []; const current = items.filter((s) => s.current).length; return { total: items.length, current, other: items.length - current } })
const cancelButton = ref<HTMLButtonElement | null>(null)
let trigger: HTMLElement | null = null
const { pending, error, notice, run } = useAction()
async function confirm(session: Schema['SessionOut'], event: Event) {
  trigger = event.currentTarget as HTMLElement
  selected.value = session
  await nextTick()
  cancelButton.value?.focus()
}
function cancel() { if (pending.value) return; selected.value = null; trigger?.focus() }
function trapFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab') return
  const buttons = [...(event.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('button:not(:disabled)')]
  const first = buttons[0]
  const last = buttons.at(-1)
  if (event.shiftKey && event.target === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && event.target === last) { event.preventDefault(); first?.focus() }
}
async function revoke() {
  const session = selected.value
  if (!session || pending.value) return
  const success = await run(async () => {
    await auth.api.DELETE('/api/v1/auth/sessions/{session_id}', { params: { path: { session_id: session.id } } })
    if (session.current) auth.clearSession()
    else await query.refetch()
  }, '会话已撤销')
  if (success) selected.value = null
}
</script>
<template>
  <section class="ws-panel mg-section sc-sessions" aria-label="登录会话">
    <div class="mg-section-head">
      <div class="mg-section-title">
        <h2 class="sc-title"><span class="sc-icon" aria-hidden="true"><Laptop /></span><span>登录会话<template v-if="query.data.value"> · {{ counts.total }}</template></span></h2>
        <p v-if="query.data.value" class="form-help">当前 {{ counts.current }} · 其他 {{ counts.other }}</p>
      </div>
      <div class="mg-section-actions"><button type="button" class="secondary-button" :disabled="query.isFetching.value" @click="query.refetch()"><RefreshCw aria-hidden="true" />刷新会话</button></div>
    </div>
    <DataState :loading="query.isPending.value" :error="query.error.value" :empty="!query.data.value?.items.length" empty-text="暂无登录会话" :stale="!!query.data.value && !isAccessError(query.error.value)" @retry="query.refetch()" />
    <ul v-if="query.data.value && !isAccessError(query.error.value)" class="sc-session-list">
      <SessionRow v-for="session in query.data.value.items" :key="session.id" :session="session" :busy="pending" @revoke="confirm" />
    </ul>
    <ActionFeedback :error="selected ? '' : error" :notice="notice" />
    <div v-if="selected" class="dialog-backdrop" @keydown.esc="cancel" @keydown="trapFocus">
      <section class="panel confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="revoke-title">
        <h2 id="revoke-title">确认撤销会话？</h2><p>{{ selected.current ? '撤销后将退出登录。' : '该会话将立即失效。' }}</p>
        <ActionFeedback :error="error" notice="" />
        <div class="form-actions"><button ref="cancelButton" class="secondary-button" :disabled="pending" @click="cancel">取消</button><button class="primary-button" :disabled="pending" @click="revoke">{{ pending ? '正在撤销…' : '确认撤销' }}</button></div>
      </section>
    </div>
  </section>
</template>
