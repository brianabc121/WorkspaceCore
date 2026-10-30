<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../stores/auth'
import { dataOf, isAccessError, type Schema } from '../services/api'
import { useAction } from './forms'
import DataState from '../components/DataState.vue'
import ActionFeedback from '../components/ActionFeedback.vue'
import { formatTime } from './time'
const auth = useAuthStore()
const query = useQuery({ queryKey: computed(() => ['sessions', auth.user?.id]), queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/auth/sessions', { signal })), enabled: computed(() => !!auth.user) })
const selected = ref<Schema['SessionOut'] | null>(null)
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
  <section class="panel form-panel">
    <div class="section-heading"><h2>登录会话</h2><button class="secondary-button" :disabled="query.isFetching.value" @click="query.refetch()">刷新会话</button></div>
    <DataState :loading="query.isPending.value" :error="query.error.value" :empty="!query.data.value?.items.length" empty-text="暂无登录会话" :stale="!!query.data.value && !isAccessError(query.error.value)" @retry="query.refetch()" />
    <ul v-if="query.data.value && !isAccessError(query.error.value)" class="record-list">
      <li v-for="session in query.data.value.items" :key="session.id">
        <div><strong>{{ session.current ? '当前会话' : '其他会话' }}</strong><p>创建：{{ formatTime(session.created_at) }}</p><p>最近活动：{{ formatTime(session.last_seen_at) }}</p><p>到期：{{ formatTime(session.expires_at) }}</p></div>
        <button class="secondary-button" :disabled="pending" @click="confirm(session, $event)">{{ session.current ? '撤销当前会话' : '撤销会话' }}</button>
      </li>
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
