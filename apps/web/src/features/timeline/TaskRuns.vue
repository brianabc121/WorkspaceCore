<script setup lang="ts">
// 任务详情里的执行记录（T-0105，方案 2.6）：开始 / 暂停（人的任务）、交给它执行 / 结束这段（实例，owner / admin）、
// 每段的时间、结束原因、token 与等待审批，以及“在时间轴中查看”。
import { computed, ref } from 'vue'
import { Pause, Play, Send, Square } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import type { Schema } from '../../services/api'
import { useRunActions, useRuns, runError } from './api'
import { STATE_LABELS, clock, dayKey, duration, tokenText } from './model'
const props = defineProps<{ task: Schema['TaskOut']; writable: boolean }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const runs = useRuns(scope, computed(() => props.task.id))
const actions = useRunActions(scope)
const items = computed(() => runs.data.value?.items ?? [])
const open = computed(() => items.value.find(r => !r.ended_at) ?? null)
const agent = computed(() => !!props.task.assignee_instance_id)
const busy = ref(false), message = ref(''), failure = ref('')
async function act(run: () => Promise<unknown>, done: string) {
  if (busy.value) return
  busy.value = true; message.value = ''; failure.value = ''
  try { await run(); message.value = done } catch (error) { failure.value = runError(error) } finally { busy.value = false }
}
const start = () => act(() => actions.start(props.task.id, agent.value), agent.value ? '已通过 IM 交给它执行' : '已开始')
const stop = (reason: Schema['TaskRunStop']['reason']) => open.value && act(() => actions.stop(props.task.id, open.value!.id, reason), reason === 'paused' ? '已暂停' : '已结束这段')
const when = computed(() => props.task.started_at ?? props.task.planned_start_at ?? props.task.due_at)
const timelineLink = computed(() => ({ path: '/tasks/timeline', query: { task: props.task.id, project: props.task.project_id, ...(when.value ? { date: dayKey(new Date(when.value)) } : {}) } }))
const waited = (r: Schema['TaskRunOut']) => r.waits.reduce((sum, w) => sum + ((w.end ? Date.parse(w.end) : Date.now()) - Date.parse(w.start)), 0)
</script>

<template>
  <section class="task-runs" aria-label="执行记录">
    <h3>执行记录</h3>
    <div class="form-actions">
      <template v-if="!task.done">
        <template v-if="agent">
          <button v-if="auth.canManage && !open" type="button" class="secondary-button" :disabled="busy || runs.isPending.value" @click="start"><Send :size="16" aria-hidden="true" />交给它执行</button>
          <button v-if="auth.canManage && open" type="button" class="secondary-button" :disabled="busy" @click="stop('interrupted')"><Square :size="16" aria-hidden="true" />结束这段</button>
          <span v-if="!auth.canManage" class="form-help">执行者是智能体：由 owner / admin 交给它执行。</span>
        </template>
        <template v-else-if="writable">
          <button v-if="!open" type="button" class="secondary-button" :disabled="busy || runs.isPending.value" @click="start"><Play :size="16" aria-hidden="true" />开始</button>
          <button v-else type="button" class="secondary-button" :disabled="busy" @click="stop('paused')"><Pause :size="16" aria-hidden="true" />暂停</button>
        </template>
      </template>
      <RouterLink :to="timelineLink">在时间轴中查看</RouterLink>
    </div>
    <p v-if="agent && auth.canManage && !open && !task.done" class="form-help">会以你的 IM 账号给它发一条消息：任务标题、task_id 与截止时间，请它读详情、做完后标为完成（完成要审批）。</p>
    <p v-if="message" class="form-help" role="status">{{ message }}</p>
    <p v-if="failure" class="feedback" role="alert">{{ failure }}</p>
    <p v-if="runs.isPending.value" class="form-help" role="status">正在加载执行记录…</p>
    <p v-else-if="!items.length" class="form-help">还没有执行记录。</p>
    <ul v-else class="task-run-list">
      <li v-for="r in items" :key="r.id">
        <span class="num">{{ clock(r.started_at) }}–{{ r.ended_at ? clock(r.ended_at) : '现在' }}（{{ duration((r.ended_at ? Date.parse(r.ended_at) : Date.now()) - Date.parse(r.started_at)) }}）</span>
        <span>{{ r.executor_name }} · {{ r.end_reason ? STATE_LABELS[r.end_reason] : '执行中' }}</span>
        <span class="num">{{ tokenText(r) }}<template v-if="waited(r) > 0"> · 等待审批 {{ duration(waited(r)) }}</template></span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.task-runs { display: flex; flex-direction: column; gap: 8px; margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--line); }
.task-runs h3 { margin: 0; font-size: 14px; }
.task-run-list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 8px; font-size: 13px; }
.task-run-list li { display: flex; flex-direction: column; gap: 1px; }
.task-run-list li span + span { color: var(--muted); font-size: 12px; }
.num { font-variant-numeric: tabular-nums; }
</style>
