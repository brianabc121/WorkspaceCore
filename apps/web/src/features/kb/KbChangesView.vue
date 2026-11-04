<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ArchiveRestore, ArrowLeft, CircleAlert, CircleCheck, RotateCcw, ShieldAlert, Trash2 } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import ModalPanel from '../../components/ModalPanel.vue'
import { CHANGE_LABELS, GUARD_LABELS, authorLabel, initial, kbError, useChanges, useKb, useRevision, useSpaces, useTrash, type Change, type ChangeFilters } from './api'
import { dateLabel, isToday, when } from './format'
import KbMobileHeader from './KbMobileHeader.vue'

// 变更记录（T-0099，效果图“电脑端 · 变更记录”）：智能体与人的每次改动、保护机制拦下的操作；
// 可以撤销最近一次修改、恢复回收站里的笔记；管理员可以解除自动暂停的删除、彻底删除。
const auth = useAuthStore(), route = useRoute(), router = useRouter(), scope = auth.workspaceId!
const canEdit = computed(() => ['owner', 'admin', 'member'].includes(auth.currentWorkspace?.role ?? ''))
const kb = useKb(scope)
const tab = computed(() => route.query.tab === 'trash' ? 'trash' : 'changes')
const kinds = [{ id: '', label: '全部' }, { id: 'create', label: '新建' }, { id: 'update', label: '修改' }, { id: 'delete', label: '删除' }, { id: 'guard', label: '拦下' }] as const
const kind = ref<ChangeFilters['kind'] | ''>(''), instance = ref('')
const filters = computed<ChangeFilters>(() => ({ ...(kind.value ? { kind: kind.value } : {}), ...(instance.value ? { instance_id: instance.value } : {}) }))
const changes = useChanges(scope, filters)
const items = computed(() => changes.data.value?.pages.flatMap(p => p.items) ?? [])
const spaces = useSpaces(scope)
const agents = computed(() => (spaces.data.value?.items ?? []).filter(s => s.kind === 'agent' && s.instance_id).map(s => ({ id: s.instance_id!, name: s.name })))
const paused = computed(() => (spaces.data.value?.items ?? []).filter(s => s.kind === 'agent' && s.deletes_paused_at))
const today = computed(() => {
  const list = items.value.filter(c => isToday(c.created_at))
  return { create: list.filter(c => c.change === 'create').length, update: list.filter(c => c.change === 'update' || c.change === 'rename').length, delete: list.filter(c => c.change === 'delete').length, guard: list.filter(c => c.kind === 'guard').length }
})

const selectedId = ref<string | null>(null), mobileDetail = ref(false)
watch(items, (list) => { if (!list.some(c => c.id === selectedId.value)) selectedId.value = list[0]?.id ?? null }, { immediate: true })
const current = computed(() => items.value.find(c => c.id === selectedId.value) ?? null)
const revNote = computed(() => current.value?.kind === 'revision' ? current.value.note_id : null)
const revVersion = computed(() => current.value?.kind === 'revision' ? current.value.version : null)
const revision = useRevision(scope, revNote, revVersion)
function pick(c: Change) { selectedId.value = c.id; mobileDetail.value = true }
const verb = (c: Change) => c.kind === 'guard' ? (c.guard ? GUARD_LABELS[c.guard] : '拦下') : (c.change ? CHANGE_LABELS[c.change] : '')
function headline(c: Change) {
  if (c.kind === 'guard') return `${c.author.name} 的${c.guard === 'secret_blocked' ? '写入被拦下' : c.guard === 'delete_paused' ? '删除被自动暂停' : c.guard === 'rate_limited' ? '写入太频繁，被限流' : '写入被拒绝（已关闭写入）'}`
  return `${authorLabel(c.author)} ${verb(c)}了《${c.note_title ?? ''}》`
}
const evidenceText: Record<string, string> = { task: '任务', project: '项目', note: '笔记', conversation: '会话', message: '消息', bookmark: '收藏文档' }

const busy = ref(false), failure = ref(''), message = ref(''), reason = ref('')
function flash(text: string) { message.value = text; setTimeout(() => { if (message.value === text) message.value = '' }, 4000) }
async function act(work: () => Promise<unknown>, done: string) {
  if (busy.value) return
  busy.value = true; failure.value = ''
  try { await work(); flash(done); reason.value = '' } catch (error) { failure.value = kbError(error) } finally { busy.value = false }
}
const undoChange = (c: Change) => c.note_id && c.version && act(() => kb.revert(c.note_id!, c.version!, c.version! - 1, reason.value.trim()), `已撤销，《${c.note_title}》回到第 ${c.version! - 1} 版的内容`)
const restoreNote = (id: string, title: string | null) => act(() => kb.restore(id), `已恢复《${title ?? ''}》`)
const resume = (spaceId: string, version: number) => act(() => kb.updateSpace(spaceId, { version, resume_deletes: true }), '已恢复删除')

const trash = useTrash(scope, computed(() => tab.value === 'trash'))
const trashItems = computed(() => trash.data.value?.pages.flatMap(p => p.items) ?? [])
const purging = ref<{ id: string; title: string } | null>(null)
async function purge() {
  const target = purging.value
  if (!target) return
  await act(() => kb.purge(target.id), `已彻底删除《${target.title}》`)
  if (!failure.value) purging.value = null
}
const setTab = (t: 'changes' | 'trash') => router.replace({ query: t === 'trash' ? { tab: 'trash' } : {} })
</script>

<template>
  <div class="kb-view" :class="mobileDetail && tab === 'changes' ? 'is-mobile-main' : 'is-mobile-list'">
    <!-- 左：变更列表 -->
    <aside class="kb-side" style="flex-basis: 380px" aria-label="变更列表">
      <KbMobileHeader title="变更记录" back="/knowledge" back-label="返回知识库" />
      <div>
        <h1 class="kb-title kb-desktop-only">变更记录</h1>
        <p class="kb-meta" style="margin: 6px 0 0; line-height: 1.6">智能体读写、修改、删除知识库都直接生效，不用审核。每次改动都记在这里，可以撤销；删除的笔记进回收站，30 天内可以恢复。</p>
      </div>
      <div class="kb-segment" role="group" aria-label="变更与回收站" style="align-self: flex-start">
        <button type="button" :aria-pressed="tab === 'changes'" @click="setTab('changes')">变更</button>
        <button type="button" :aria-pressed="tab === 'trash'" @click="setTab('trash')">回收站</button>
      </div>
      <div v-for="s in paused" :key="s.id" class="kb-notice is-warning" role="alert">
        <ShieldAlert aria-hidden="true" />
        <span class="kb-grow">{{ s.name }} 的删除在 {{ when(s.deletes_paused_at) }}被自动暂停（1 小时内删除太多）。核对下面的删除记录后再恢复。</span>
        <button v-if="auth.canManage" type="button" class="kb-btn is-small" :disabled="busy" @click="resume(s.id, s.version)">恢复删除</button>
      </div>
      <template v-if="tab === 'changes'">
        <p class="kb-meta kb-num" style="margin: 0">今天：新建 {{ today.create }} · 修改 {{ today.update }} · 删除 {{ today.delete }} · 拦下 {{ today.guard }}</p>
        <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center">
          <div class="kb-segment" role="group" aria-label="按操作筛选">
            <button v-for="k in kinds" :key="k.id" type="button" :aria-pressed="kind === k.id" @click="kind = k.id">{{ k.label }}</button>
          </div>
          <label class="kb-check"><span class="kb-sr">按智能体筛选</span>
            <select v-model="instance" class="kb-select"><option value="">所有作者</option><option v-for="a in agents" :key="a.id" :value="a.id">{{ a.name }}</option></select>
          </label>
        </div>
        <p v-if="changes.isPending.value" class="kb-meta" role="status">正在加载…</p>
        <p v-else-if="changes.error.value" class="kb-error" role="alert">{{ kbError(changes.error.value) }}</p>
        <ul class="kb-list" style="gap: 4px">
          <li v-for="c in items" :key="c.id">
            <button type="button" class="kb-change" :aria-pressed="c.id === selectedId" @click="pick(c)">
              <span class="kb-avatar" :class="{ 'is-user': c.author.kind !== 'agent' }" aria-hidden="true">{{ initial(c.author.name) }}</span>
              <span class="kb-stack" style="flex: 1">
                <span><span class="kb-verb">{{ verb(c) }}</span>{{ c.kind === 'guard' ? c.detail : c.note_title }}</span>
                <span class="kb-meta" style="display: flex; align-items: center; gap: 4px">
                  <CircleAlert v-if="c.kind === 'guard'" aria-hidden="true" style="width: 13px; height: 13px; stroke: var(--kb-warning)" />
                  {{ c.author.name }} · {{ c.space_name ?? '' }} · {{ when(c.created_at) }}<template v-if="c.kind === 'revision'"> · <span class="kb-num">+{{ c.added }} −{{ c.removed }}</span></template>
                </span>
              </span>
            </button>
          </li>
        </ul>
        <p v-if="!changes.isPending.value && !items.length" class="kb-empty">还没有改动记录。</p>
        <button v-if="changes.hasNextPage.value" type="button" class="kb-btn" :disabled="changes.isFetchingNextPage.value" @click="changes.fetchNextPage()">加载更早的</button>
      </template>
      <template v-else>
        <p v-if="trash.isPending.value" class="kb-meta" role="status">正在加载…</p>
        <p v-else-if="trash.error.value" class="kb-error" role="alert">{{ kbError(trash.error.value) }}</p>
        <ul class="kb-list" style="gap: 6px">
          <li v-for="t in trashItems" :key="t.id" class="kb-card" style="padding: 12px; gap: 6px">
            <span style="display: flex; align-items: center; gap: 8px"><span class="kb-dot is-small" :class="`k-${t.space_kind}`" aria-hidden="true" /><strong style="flex: 1">{{ t.title }}</strong></span>
            <span class="kb-meta">{{ t.space_name }} · {{ authorLabel(t.deleted_by) }} {{ when(t.deleted_at) }}删除 · {{ dateLabel(t.purge_after) }}彻底删除</span>
            <span style="display: flex; gap: 8px">
              <RouterLink :to="`/knowledge/notes/${t.id}`" class="kb-btn is-small">查看</RouterLink>
              <button v-if="canEdit" type="button" class="kb-btn is-small" :disabled="busy" @click="restoreNote(t.id, t.title)"><ArchiveRestore aria-hidden="true" />恢复</button>
              <button v-if="auth.canManage" type="button" class="kb-btn is-small is-danger" :disabled="busy" @click="purging = { id: t.id, title: t.title }"><Trash2 aria-hidden="true" />彻底删除</button>
            </span>
          </li>
        </ul>
        <p v-if="!trash.isPending.value && !trashItems.length" class="kb-empty">回收站是空的。</p>
        <button v-if="trash.hasNextPage.value" type="button" class="kb-btn" @click="trash.fetchNextPage()">加载更多</button>
      </template>
      <p v-if="failure" class="kb-error" role="alert">{{ failure }}</p>
    </aside>

    <!-- 右：变更详情 -->
    <main class="kb-main" aria-label="变更详情">
      <div v-if="tab === 'changes' && current" class="kb-page" style="max-width: 860px">
        <button type="button" class="kb-btn is-small kb-mobile-only" style="align-self: flex-start" @click="mobileDetail = false"><ArrowLeft aria-hidden="true" />返回列表</button>
        <header class="kb-detail-head">
          <span class="kb-avatar is-large" :class="{ 'is-user': current.author.kind !== 'agent' }" aria-hidden="true">{{ initial(current.author.name) }}</span>
          <div class="kb-stack">
            <h2>{{ headline(current) }}</h2>
            <p class="kb-meta" style="margin: 0; display: flex; flex-wrap: wrap; align-items: center; gap: 6px">
              <span v-if="current.space_kind" class="kb-chip"><span class="kb-dot is-small" :class="`k-${current.space_kind}`" aria-hidden="true" />{{ current.space_name }}</span>
              {{ when(current.created_at) }}<template v-if="current.version"> · 第 {{ current.version }} 版</template>
            </p>
          </div>
        </header>
        <div v-if="current.kind === 'guard'" class="kb-notice is-warning" role="note">
          <ShieldAlert aria-hidden="true" />
          <span class="kb-grow">{{ current.detail }}{{ current.note_title ? `（《${current.note_title}》）` : '' }}。{{ current.guard === 'secret_blocked' ? '工作台没有保存这次写入，内容也没有记下来；已提醒它改用密钥库里的名字。' : current.guard === 'delete_paused' ? '这个知识库的删除已暂停，管理员确认后可以恢复。' : '' }}</span>
        </div>
        <div v-if="current.note_deleted && current.kind === 'revision'" class="kb-notice" role="note"><Trash2 aria-hidden="true" /><span class="kb-grow">这篇笔记现在在回收站里。</span><button v-if="canEdit && current.note_id" type="button" class="kb-btn is-small" :disabled="busy" @click="restoreNote(current.note_id!, current.note_title)"><ArchiveRestore aria-hidden="true" />恢复</button></div>
        <section v-if="current.kind === 'revision'" class="kb-card" aria-label="写入原因">
          <dl class="kb-dl">
            <dt>原因</dt><dd>{{ current.reason || '（没有写原因）' }}</dd>
            <template v-if="revision.data.value?.evidence.length"><dt>依据</dt><dd style="display: flex; flex-wrap: wrap; gap: 6px"><template v-for="e in revision.data.value.evidence" :key="e.kind + e.id"><RouterLink v-if="e.kind === 'note'" :to="`/knowledge/notes/${e.id}`" class="kb-tag">笔记</RouterLink><RouterLink v-else-if="e.kind === 'task'" :to="`/tasks?task=${e.id}`" class="kb-tag">任务</RouterLink><span v-else class="kb-badge">{{ evidenceText[e.kind] }} {{ e.id.slice(0, 8) }}</span></template></dd></template>
          </dl>
        </section>
        <section v-if="current.kind === 'revision'" class="kb-card" aria-label="自动检查">
          <h3 style="font-size: 14px">自动检查 <span class="kb-meta" style="font-weight: 400">只有发现密钥会拦下写入，其余只提醒</span></h3>
          <ul class="kb-checks">
            <li class="is-ok"><CircleCheck aria-hidden="true" /><span>没有发现密钥或密码</span></li>
            <li class="is-ok"><CircleCheck aria-hidden="true" /><span>{{ current.author.kind === 'agent' ? '写入者有这个知识库的写权限，没有超过每小时的写入上限' : current.author.kind === 'system' ? '工作台自动维护（《收到的纠正》）' : '人写入，按工作空间角色校验' }}</span></li>
          </ul>
        </section>
        <section v-if="current.kind === 'revision' && current.change !== 'delete' && current.change !== 'restore'" class="kb-card" aria-label="改动内容">
          <div class="kb-card-head"><h3 style="font-size: 14px">改动内容</h3><span class="kb-meta kb-num">+{{ current.added }} −{{ current.removed }}</span></div>
          <p v-if="revision.isPending.value" class="kb-meta" role="status">正在加载…</p>
          <div v-else-if="revision.data.value" class="kb-diff">
            <div v-for="(d, i) in revision.data.value.diff" :key="i" :class="{ 'is-add': d.sign === '+', 'is-del': d.sign === '-' }"><span class="kb-sign" aria-hidden="true">{{ d.sign }}</span><span><span class="kb-sr">{{ d.sign === '+' ? '新增：' : d.sign === '-' ? '删除：' : '' }}</span>{{ d.text || ' ' }}</span></div>
            <div v-if="!revision.data.value.diff.length"><span>正文没有变化（只改了标题或属性）</span></div>
          </div>
        </section>
        <div v-if="canEdit && current.kind === 'revision'" style="display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end">
          <label v-if="current.revertable && current.author.kind !== 'system'" class="kb-field" style="flex: 1 1 260px">撤销原因（会写回智能体的《收到的纠正》）<input v-model="reason" class="kb-input" maxlength="500"></label>
          <button v-if="current.revertable && current.author.kind !== 'system'" type="button" class="kb-btn is-primary" :disabled="busy" @click="undoChange(current)"><RotateCcw aria-hidden="true" />撤销这次修改</button>
          <RouterLink v-if="current.note_id && !current.note_deleted" :to="`/knowledge/notes/${current.note_id}`" class="kb-btn">打开笔记</RouterLink>
        </div>
        <p class="kb-meta" style="margin: 0">撤销会生成一个新版本，并把原因写回写入的智能体的《收到的纠正》，它下次读写知识库时会先看到。</p>
      </div>
      <div v-else-if="tab === 'changes'" class="kb-page"><p class="kb-empty">选一条变更查看详情。</p></div>
      <div v-else class="kb-page"><p class="kb-empty">回收站里的笔记 30 天后彻底删除。恢复后会回到原来的知识库。</p></div>
    </main>
    <ModalPanel v-if="purging" title="彻底删除" :busy="busy" @close="purging = null">
      <p>《{{ purging.title }}》和它的全部版本会被删除，不能恢复。</p>
      <div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px">
        <button type="button" class="kb-btn" :disabled="busy" @click="purging = null">取消</button>
        <button type="button" class="kb-btn is-primary" style="background: var(--kb-danger); border-color: var(--kb-danger)" :disabled="busy" @click="purge">彻底删除</button>
      </div>
    </ModalPanel>
    <p v-if="message" class="kb-toast" role="status">{{ message }}</p>
  </div>
</template>
