<script setup lang="ts">
// 任务的动态与打回（T-0111，docs/v2/AGENT_OPS_PLAN.md 第 3.6 节）：
// 动态 = 设为自由接取、领取、放弃、完成、打回（note 是智能体或人写的文字，按纯文本显示）；
// 打回只给 owner / admin：已完成的任务回到进行中，原因经 IM 发给执行的智能体。
// 待查看（T-0164）：智能体完成的任务等 owner / admin 看过结果后“确认结果”，首页“已完成待查看”随之减少。
import { computed, ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, errorMessage, type Schema } from '../../services/api'
import { formatTime } from '../time'

const props = defineProps<{ task: Schema['TaskOut'] }>()
const auth = useAuthStore(), cache = useQueryClient(), scope = auth.workspaceId!, header = { 'X-Workspace-Id': scope }
const admin = computed(() => ['owner', 'admin'].includes(auth.currentWorkspace?.role ?? ''))
const events = useQuery({
  queryKey: computed(() => ['task-events', auth.user?.id, scope, props.task.id, props.task.version]),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/tasks/{task_id}/events', { params: { header, path: { task_id: props.task.id } }, signal })),
})
const items = computed(() => events.data.value?.items ?? [])
const verbs: Record<string, string> = { opened: '设为可自由接取', claimed: '领取了任务', released: '放弃了任务', completed: '完成了任务', reopened: '打回了任务', reviewed: '确认了结果' }
// 最近一次完成说明：待查看时放在“确认结果”上方，看完再确认
const completion = computed(() => items.value.find((e) => e.kind === 'completed') ?? null)
const reason = ref(''), busy = ref(false), problem = ref(''), notice = ref('')
async function reopen() {
  const text = reason.value.trim()
  if (!text) { problem.value = '请写打回的原因'; return }
  if (busy.value) return
  busy.value = true; problem.value = ''; notice.value = ''
  try {
    dataOf(await auth.api.POST('/api/v1/tasks/{task_id}/reopen', { params: { header, path: { task_id: props.task.id } }, body: { version: props.task.version, reason: text } }))
    reason.value = ''; notice.value = '已打回：任务回到进行中，原因已发给执行者'
    await refresh()
  } catch (cause) {
    problem.value = cause instanceof ApiError && cause.code === 'VERSION_CONFLICT' ? '任务已在别处修改，请重新打开后再打回。' : errorMessage(cause)
  } finally { busy.value = false }
}
function refresh() {
  return cache.invalidateQueries({ predicate: (q) => ['tasks', 'task', 'timeline', 'task-runs', 'task-events', 'overview', 'task-progress'].includes(String(q.queryKey[0])) && q.queryKey[2] === scope })
}
async function review() {
  if (busy.value) return
  busy.value = true; problem.value = ''; notice.value = ''
  try {
    dataOf(await auth.api.POST('/api/v1/tasks/{task_id}/review', { params: { header, path: { task_id: props.task.id } }, body: { version: props.task.version } }))
    notice.value = '已确认结果'
    await refresh()
  } catch (cause) {
    problem.value = cause instanceof ApiError && cause.code === 'VERSION_CONFLICT' ? '任务已在别处修改，请重新打开后再确认。' : errorMessage(cause)
  } finally { busy.value = false }
}
</script>
<template>
  <section class="task-claim" aria-label="任务动态">
    <p v-if="task.claimable" class="chip">可自由接取 · {{ task.claim_scope === 'project' ? '只限项目成员' : '全部智能体' }}</p>
    <div v-if="task.review_pending" class="form-panel task-review" aria-label="待查看">
      <p><span class="chip chip-high">已完成 · 待查看</span> 智能体完成了这个任务，看过结果后确认；需要返工就打回。</p>
      <p v-if="completion?.note" class="note-text">完成说明：{{ completion.note }}</p>
      <button v-if="admin" type="button" class="primary-button" :disabled="busy" @click="review">确认结果</button>
      <p v-else class="form-help">owner / admin 确认结果。</p>
    </div>
    <form v-if="admin && task.done" class="form-panel" aria-label="打回任务" novalidate @submit.prevent="reopen">
      <label class="form-field">打回原因<textarea v-model="reason" maxlength="500" rows="2" :disabled="busy" placeholder="例如：缺少回滚步骤"></textarea></label>
      <button class="secondary-button" :disabled="busy">打回到进行中</button>
      <p v-if="problem" role="alert">{{ problem }}</p>
    </form>
    <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
    <template v-if="items.length">
      <h3>动态</h3>
      <ul class="task-events">
        <li v-for="e in items" :key="e.id"><span class="form-help">{{ formatTime(e.created_at) }}</span> {{ e.actor_name || '系统' }} {{ verbs[e.kind] ?? e.kind }}<template v-if="e.note">：<span class="note-text">{{ e.note }}</span></template></li>
      </ul>
    </template>
  </section>
</template>
