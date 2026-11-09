<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { CalendarClock, Flag, FolderKanban, PlayCircle, User, X } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf, errorMessage, type Schema } from '../../services/api'
import type { TaskCommand } from '../../stores/taskOperations'
import { formatTime } from '../time'
import { dueInfo, priorities } from './shared'
import { STATE_META, markViewed, setBlocked, stateError, useTaskRefresh } from './workState'
import TaskEditor from './TaskEditor.vue'

// 任务详情（T-0167，效果图 01-tasks 右侧；规范第 4.1 节）：状态、阻塞原因、待查看的完成说明与“标为已查看”、
// 关键信息，下面沿用原来的编辑器（README、讨论、执行记录、动态、相关文档都还在）。
// 打开详情不会自动标为已查看；阻塞原因与完成说明来自人或智能体，按纯文本显示。
const props = defineProps<{ task?: Schema['TaskOut']; projects: Schema['ProjectOut'][]; writable: boolean; locked: boolean; names: Map<string, string>; closable?: boolean }>()
const emit = defineEmits<{ submit: [command: TaskCommand]; close: [] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, header = { 'X-Workspace-Id': scope }
const refresh = useTaskRefresh(scope)
const view = ref<'detail' | 'readme' | 'discussion'>('detail')
const state = computed(() => (props.task ? STATE_META[props.task.work_state] : null))
const project = computed(() => props.projects.find((p) => p.id === props.task?.project_id)?.title ?? '已归档的项目')
const executor = computed(() => {
  const task = props.task
  if (!task) return ''
  const id = task.assignee_user_id ?? task.assignee_instance_id
  if (id) return props.names.get(id) ?? (task.assignee_instance_id ? '智能体' : '成员')
  return task.claimable ? '可自由接取' : '未分配'
})
const due = computed(() => (props.task ? dueInfo(props.task.due_at, props.task.done) : null))
// 最近一次完成说明（待查看时给出“看什么”）：与任务动态同一个查询
const events = useQuery({
  queryKey: computed(() => ['task-events', user, scope, props.task?.id, props.task?.version]),
  enabled: computed(() => props.task?.work_state === 'awaiting_view'),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/tasks/{task_id}/events', { params: { header, path: { task_id: props.task!.id } }, signal })),
})
const completion = computed(() => events.data.value?.items.find((e) => e.kind === 'completed') ?? null)

const busy = ref(false), problem = ref(''), notice = ref('')
const editingBlock = ref(false), reason = ref('')
watch(() => props.task?.id, () => { editingBlock.value = false; problem.value = ''; notice.value = ''; view.value = 'detail' })
async function run(action: () => Promise<unknown>, done: string) {
  if (busy.value) return
  busy.value = true; problem.value = ''; notice.value = ''
  try {
    await action()
    notice.value = done
    editingBlock.value = false
  } catch (cause) {
    problem.value = stateError(cause) ?? errorMessage(cause)
  } finally {
    busy.value = false
    await refresh()
  }
}
const viewResult = () => run(() => markViewed(scope, props.task!), '已标为已查看（只影响你自己）')
function startBlock() { reason.value = props.task?.blocker?.reason ?? ''; editingBlock.value = true }
function saveBlock() {
  const text = reason.value.trim()
  if (!text) { problem.value = '请写明阻塞原因'; return }
  void run(() => setBlocked(scope, props.task!, true, text), props.task?.blocker ? '已修改阻塞原因' : '已标记阻塞')
}
const unblock = () => run(() => setBlocked(scope, props.task!, false), '已解除阻塞')
</script>

<template>
  <section class="task-detail" :aria-label="task ? `任务详情：${task.title}` : '新建任务'">
    <header v-if="task" class="task-detail-head">
      <div class="task-detail-title">
        <h2>{{ task.title }}</h2>
        <span v-if="state" class="ws-state" :class="`state-${task.work_state}`"><component :is="state.icon" aria-hidden="true" />{{ state.label }}</span>
      </div>
      <button v-if="closable" type="button" class="shell-icon" aria-label="关闭任务详情" @click="emit('close')"><X aria-hidden="true" /></button>
    </header>

    <template v-if="task">
      <div v-if="task.blocker && !editingBlock" class="ws-callout" role="note" aria-label="阻塞原因">
        <strong><component :is="STATE_META.blocked.icon" aria-hidden="true" />阻塞原因</strong>
        <p>{{ task.blocker.reason }}</p>
        <p class="form-help">{{ task.blocker.blocked_by_name || '成员' }} 标记于 {{ formatTime(task.blocker.blocked_at) }}<template v-if="task.active_run"> · 标记阻塞不会停止正在执行的一段</template></p>
        <div v-if="writable" class="form-actions"><button type="button" class="secondary-button" :disabled="busy" @click="startBlock">修改原因</button><button type="button" class="primary-button" :disabled="busy" @click="unblock">解除阻塞</button></div>
      </div>
      <form v-if="editingBlock" class="ws-callout" aria-label="阻塞原因" @submit.prevent="saveBlock">
        <label class="form-field">阻塞原因<textarea v-model="reason" maxlength="2000" rows="3" :disabled="busy" placeholder="例如：等待补充 GitHub 仓库访问权限"></textarea></label>
        <p class="form-help">写清楚卡在哪里、需要谁做什么。标记阻塞不会停止正在执行的一段，解除后也不会自动重新开始。</p>
        <div class="form-actions"><button class="primary-button" :disabled="busy || !reason.trim()">{{ task.blocker ? '保存原因' : '标记阻塞' }}</button><button type="button" class="secondary-button" :disabled="busy" @click="editingBlock = false">取消</button></div>
      </form>

      <div v-if="task.work_state === 'awaiting_view'" class="ws-callout tone-amber" role="note" aria-label="待查看">
        <strong><component :is="STATE_META.awaiting_view.icon" aria-hidden="true" />已完成 · 待查看</strong>
        <p v-if="completion?.note">完成说明：{{ completion.note }}</p>
        <p v-else-if="events.isPending.value" class="form-help">正在读取完成说明…</p>
        <p v-else class="form-help">没有写完成说明，请看 README 与执行记录。</p>
        <div class="form-actions">
          <button type="button" class="primary-button" :disabled="busy" @click="viewResult">标为已查看</button>
          <button type="button" class="secondary-button" @click="view = 'readme'">查看 README</button>
        </div>
        <p class="form-help">“已查看”只影响你自己；需要返工就在下面的动态里打回。</p>
      </div>

      <p v-if="task.active_run" class="task-running"><PlayCircle aria-hidden="true" />正在执行：{{ task.active_run.executor_name }}，开始于 {{ formatTime(task.active_run.started_at) }}</p>

      <dl class="ws-facts">
        <dt><FolderKanban aria-hidden="true" />项目</dt><dd>{{ project }}</dd>
        <dt><User aria-hidden="true" />执行者</dt><dd>{{ executor }}</dd>
        <dt><Flag aria-hidden="true" />优先级</dt><dd>{{ priorities[task.priority] }}</dd>
        <dt><CalendarClock aria-hidden="true" />截止时间</dt><dd>{{ due?.text }}</dd>
      </dl>
      <div v-if="writable && !task.done && !task.blocker && !editingBlock" class="form-actions"><button type="button" class="secondary-button" :disabled="busy" @click="startBlock">标记阻塞</button></div>
      <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
      <p v-if="problem" role="alert" class="feedback">{{ problem }}</p>
    </template>

    <TaskEditor v-model:view="view" :task="task" :projects="projects" :writable="writable" :locked="locked" @submit="emit('submit', $event)" />
  </section>
</template>
