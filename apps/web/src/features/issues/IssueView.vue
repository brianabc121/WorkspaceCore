<script setup lang="ts">
// Issue 详情（T-0124，方案 8.8）：正文、时间线（评论和事件按时间排列）；侧栏是指派、标签、关联任务、PR 与参与者。
// 正文和评论是人或智能体写的，按不可信内容显示：沿用知识库的安全 Markdown 渲染，不执行 HTML。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { ApiError, errorMessage } from '../../services/api'
import { formatTime } from '../time'
import { issueHtml } from './format'
import { CLOSE_REASONS, KIND_LABELS, actorName, useIssueActions, type CloseReason, type IssueComment, type IssueDetail, type IssueEvent } from './api'
import '../kb/kb.css'

const props = defineProps<{ issue: IssueDetail }>()
const auth = useAuthStore(), scope = auth.workspaceId!, router = useRouter(), actions = useIssueActions(scope)
const writable = computed(() => !!auth.currentWorkspace && auth.currentWorkspace.role !== 'viewer')
const html = (body: string) => issueHtml(body, props.issue.project_id)
function follow(event: MouseEvent | KeyboardEvent) {
  const link = (event.target as HTMLElement | null)?.closest('a'), href = link?.getAttribute('href')
  if (!href || !href.startsWith('/') || event.ctrlKey || event.metaKey || event.shiftKey) return
  event.preventDefault(); void router.push(href)
}
onMounted(() => { if (props.issue.unread) void actions.read(props.issue.id).catch(() => undefined) })

type Entry = { kind: 'comment'; at: string; comment: IssueComment } | { kind: 'event'; at: string; event: IssueEvent }
const timeline = computed<Entry[]>(() => [
  ...props.issue.comments.map((comment) => ({ kind: 'comment' as const, at: comment.created_at, comment })),
  ...props.issue.events.map((event) => ({ kind: 'event' as const, at: event.created_at, event })),
].sort((a, b) => a.at.localeCompare(b.at)))
const names = computed(() => new Map(props.issue.assignees.map((a) => [a.id, actorName(a)])))
const who = (id: unknown) => names.value.get(String(id ?? '')) ?? '一位成员'
function describe(e: IssueEvent) {
  const d = e.data
  switch (e.kind) {
    case 'opened': return '开了这个 Issue'
    case 'closed': return `关闭了（${CLOSE_REASONS[d.reason as CloseReason] ?? d.reason ?? ''}）`
    case 'reopened': return '重新打开了'
    case 'assigned': return `指派给 ${who(d.instance_id ?? d.user_id)}`
    case 'unassigned': return `取消指派 ${who(d.instance_id ?? d.user_id)}`
    case 'labeled': return `加了标签「${d.label ?? ''}」`
    case 'unlabeled': return `去掉标签「${d.label ?? ''}」`
    case 'linked': return `关联了任务《${d.title ?? ''}》`
    case 'unlinked': return '取消关联了一个任务'
    case 'retitled': return `改了标题（原来是「${d.from ?? ''}」）`
    case 'locked': return '锁定了（只有人能评论）'
    case 'unlocked': return '解除了锁定'
    case 'mentioned': return `在 #${d.number ?? ''} 里提到了这里`
    default: return e.kind
  }
}
const participants = computed(() => [...new Map([props.issue.author, ...props.issue.comments.map((c) => c.author)].map((a) => [`${a.kind}:${a.id}`, a])).values()])

const reply = ref(''), busy = ref(false), problem = ref(''), notice = ref('')
const reason = ref<CloseReason>('resolved'), duplicate = ref<number | null>(null)
async function run(task: () => Promise<unknown>, done: string) {
  if (busy.value) return
  busy.value = true; problem.value = ''; notice.value = ''
  try { await task(); notice.value = done } catch (cause) {
    problem.value = cause instanceof ApiError && cause.code === 'VERSION_CONFLICT' ? 'Issue 已在别处修改，请刷新后再试。'
      : cause instanceof ApiError && cause.code === 'SECRET_BLOCKED' ? '内容里像是有令牌、私钥一类的密钥，没有保存；去掉之后再提交。' : errorMessage(cause)
  } finally { busy.value = false }
}
function send() {
  if (!reply.value.trim()) { problem.value = '写点内容再发'; return }
  return run(async () => { await actions.comment(props.issue.id, reply.value.trim()); reply.value = '' }, '已评论')
}
const close = () => run(() => actions.update(props.issue, { state: 'closed', close_reason: reason.value, ...(reason.value === 'duplicate' ? { duplicate_of: duplicate.value } : {}) }), '已关闭')
const reopen = () => run(() => actions.update(props.issue, { state: 'open' }), '已重新打开')
const lock = (locked: boolean) => run(() => actions.update(props.issue, { locked }), locked ? '已锁定' : '已解除锁定')
const hide = (comment: IssueComment) => run(() => actions.hide(props.issue.id, comment.id), '已隐藏这条评论')
async function remove() {
  if (!window.confirm(`删除 #${props.issue.number}？删除后进回收站。`)) return
  await run(async () => { await actions.remove(props.issue.id); await router.push(`/projects/${props.issue.project_id}?tab=issues`) }, '已删除')
}
</script>

<template>
  <article class="issue-view" :aria-label="`Issue #${issue.number}`">
    <header class="issue-head">
      <h1><span class="issue-number">#{{ issue.number }}</span> {{ issue.title }}</h1>
      <p class="issue-meta">
        <span class="badge">{{ issue.state === 'open' ? '未关闭' : `已关闭（${CLOSE_REASONS[issue.close_reason!]}）` }}</span>
        <span class="badge">{{ KIND_LABELS[issue.kind] }}</span>
        <span v-if="issue.locked" class="badge">已锁定</span>
        <span v-if="issue.agent_paused" class="badge">智能体暂停评论（等人评论后恢复）</span>
        <span class="form-help">{{ actorName(issue.author) }} 开于 {{ formatTime(issue.created_at) }}</span>
        <RouterLink :to="`/projects/${issue.project_id}?tab=issues`">项目的全部 Issue</RouterLink>
      </p>
    </header>
    <div class="issue-layout">
      <div class="issue-main">
        <section class="panel kb-md" aria-label="正文" @click="follow" @keydown.enter="follow">
          <!-- eslint-disable-next-line vue/no-v-html -- issueHtml 已用 DOMPurify 消毒 -->
          <div v-if="issue.body.trim()" v-html="html(issue.body)"></div>
          <p v-else class="form-help">没有正文。</p>
        </section>
        <ol class="issue-timeline" aria-label="时间线">
          <li v-for="entry in timeline" :key="entry.kind === 'comment' ? entry.comment.id : entry.event.id" :class="entry.kind">
            <template v-if="entry.kind === 'comment'">
              <div class="issue-comment-head">
                <strong>{{ actorName(entry.comment.author) }}</strong>
                <span class="form-help">{{ formatTime(entry.comment.created_at) }}<template v-if="entry.comment.edited_at"> · 编辑过</template></span>
                <button v-if="issue.can_moderate && !entry.comment.hidden" type="button" class="secondary-button" :disabled="busy" @click="hide(entry.comment)">隐藏</button>
              </div>
              <p v-if="entry.comment.hidden" class="form-help">（这条评论已被管理员隐藏）</p>
              <!-- eslint-disable-next-line vue/no-v-html -- issueHtml 已用 DOMPurify 消毒 -->
              <div v-else class="kb-md" @click="follow" @keydown.enter="follow" v-html="html(entry.comment.body ?? '')"></div>
            </template>
            <p v-else class="form-help">{{ actorName(entry.event.actor) }} {{ describe(entry.event) }} · {{ formatTime(entry.event.created_at) }}</p>
          </li>
        </ol>
        <form v-if="writable" class="form-panel" aria-label="评论" novalidate @submit.prevent="send">
          <label class="form-field">评论（Markdown；@名字 提到人或智能体）<textarea v-model="reply" rows="4" maxlength="10000" :disabled="busy"></textarea></label>
          <button class="primary-button" :disabled="busy">评论</button>
        </form>
        <p v-if="problem" role="alert">{{ problem }}</p>
        <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
      </div>
      <aside class="issue-side" aria-label="Issue 信息">
        <h2>指派</h2>
        <p v-if="!issue.assignees.length" class="form-help">还没有指派</p>
        <ul v-else><li v-for="a in issue.assignees" :key="`${a.kind}:${a.id}`">{{ actorName(a) }}</li></ul>
        <h2>标签</h2>
        <p v-if="!issue.labels.length" class="form-help">没有标签</p>
        <p v-else><span v-for="label in issue.labels" :key="label" class="chip">{{ label }}</span></p>
        <h2>关联的任务</h2>
        <p v-if="!issue.tasks.length" class="form-help">没有关联任务</p>
        <ul v-else><li v-for="t in issue.tasks" :key="t.id"><RouterLink :to="`/tasks?task=${t.id}`">{{ t.title }}</RouterLink><template v-if="t.done">（已完成）</template></li></ul>
        <template v-if="issue.pulls.length">
          <h2>Pull requests</h2>
          <ul><li v-for="p in issue.pulls" :key="p.url"><a :href="p.url" target="_blank" rel="noopener noreferrer">{{ p.title }}</a> · {{ p.merged ? '已合并' : p.state === 'open' ? '打开' : p.state === 'closed' ? '已关闭' : '状态未知' }}</li></ul>
        </template>
        <h2>参与者</h2>
        <ul><li v-for="a in participants" :key="`${a.kind}:${a.id}`">{{ actorName(a) }}</li></ul>
        <div v-if="issue.can_close || issue.can_moderate" class="issue-actions">
          <form v-if="issue.can_close && issue.state === 'open'" aria-label="关闭 Issue" class="form-panel" @submit.prevent="close">
            <label class="form-field">关闭原因<select v-model="reason" :disabled="busy"><option v-for="(label, key) in CLOSE_REASONS" :key="key" :value="key">{{ label }}</option></select></label>
            <label v-if="reason === 'duplicate'" class="form-field">重复的是哪一个（编号）<input v-model.number="duplicate" type="number" min="1" :disabled="busy"></label>
            <button class="secondary-button" :disabled="busy || (reason === 'duplicate' && !duplicate)">关闭</button>
          </form>
          <button v-if="issue.can_close && issue.state === 'closed'" type="button" class="secondary-button" :disabled="busy" @click="reopen">重新打开</button>
          <button v-if="issue.can_moderate" type="button" class="secondary-button" :disabled="busy" @click="lock(!issue.locked)">{{ issue.locked ? '解除锁定' : '锁定' }}</button>
          <button v-if="issue.can_moderate" type="button" class="secondary-button" :disabled="busy" @click="remove">删除</button>
        </div>
      </aside>
    </div>
  </article>
</template>

<style scoped>
.issue-view { display: flex; flex-direction: column; gap: 12px; }
.issue-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.issue-number { color: var(--muted, #666); }
.issue-layout { display: grid; grid-template-columns: minmax(0, 1fr) 260px; gap: 16px; }
@media (max-width: 800px) { .issue-layout { grid-template-columns: 1fr; } }
.issue-main { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.issue-timeline { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.issue-timeline li.comment { border: 1px solid var(--border, #ddd); border-radius: 8px; padding: 8px 12px; }
.issue-comment-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.issue-side h2 { font-size: 0.95rem; margin: 12px 0 4px; }
.issue-side ul { margin: 0; padding-left: 18px; }
.issue-actions { display: flex; flex-direction: column; gap: 8px; margin-top: 16px; }
:deep(.issue-mention) { font-weight: 600; }
</style>
