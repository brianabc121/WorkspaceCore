<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { useQueryClient } from '@tanstack/vue-query'
import { Archive, ArrowUpRight, BookMarked, CircleAlert, FileText, Plus, RefreshCw, Search, SquarePen, Trash2, X } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError } from '../../services/api'
import { useMediaQuery } from '../../composables/useMediaQuery'
import { NOTE_TYPE_LABELS, SPACE_KIND_LABELS, authorLabel, kbError, kbKey, useKnowledgeOverview, useNote, useSpaces, type GraphNode, type Note } from './api'
import { excerpt, nodeHref, statusOf, weekWindow, when } from './format'
import KbMobileHeader from './KbMobileHeader.vue'
import KbOverviewPreview from './KbOverviewPreview.vue'
import KbOverviewStats from './KbOverviewStats.vue'
import KbSpaceList, { spaceLink } from './KbSpaceList.vue'
import KbTabs from './KbTabs.vue'

// 知识总览（T-0173，二批四页改造规范 4.1–4.3，效果图 01）：/knowledge 的首页。
// 左栏知识库导航（点一个只看它，再点或点“全部”回来）；主区四个真实统计、所选笔记的局部关联（depth=1）、
// 所选笔记的详情、最近更新。数字只来自 GET /knowledge/overview；完整图谱在 /knowledge/graph。
// 默认选中最近更新的一篇；一律按 ID 选，同名笔记不会串。读不到（404 / 失权）的笔记清掉详情，不显示上一篇。
// 手机上（≤760px）是知识库首页：统计、知识库列表、最近更新；局部关联和详情不加载，点笔记直接打开。
const auth = useAuthStore(), router = useRouter(), cache = useQueryClient(), scope = auth.workspaceId!
const canEdit = computed(() => ['owner', 'admin', 'member'].includes(auth.currentWorkspace?.role ?? ''))
const wide = useMediaQuery('(min-width: 761px)')

const spaces = useSpaces(scope)
const spaceItems = computed(() => spaces.data.value?.items ?? [])
const spaceId = ref<string | null>(null)
const currentSpace = computed(() => (spaceId.value ? spaceItems.value.find(s => s.id === spaceId.value) ?? null : null))
const scopeLabel = computed(() => (!spaceId.value ? '全部知识库' : currentSpace.value ? `${currentSpace.value.name}${currentSpace.value.archived ? '（已归档）' : ''}` : '所选知识库'))

// 本周：显示时区（浏览器）的周一 00:00 到下周一 00:00；查询键里带着它
const week = weekWindow()
const overview = useKnowledgeOverview(scope, () => ({ ...(spaceId.value ? { space_id: spaceId.value } : {}), ...week, recent_limit: 6 }))
const failed = computed(() => !!overview.error.value)
const summary = computed(() => (failed.value ? undefined : overview.data.value?.summary))
const recent = computed(() => (failed.value ? [] : overview.data.value?.recent_notes ?? []))

// 所选笔记：用户点过的优先；否则是最近更新的第一篇还读得到的。换知识库时重新选
const gone = (error: unknown) => error instanceof ApiError && (error.status === 404 || error.status === 403)
const picked = ref<string | null>(null), lost = ref<string[]>([])
watch(spaceId, () => { picked.value = null; lost.value = [] })
const selectedId = computed(() => picked.value ?? recent.value.find(n => !lost.value.includes(n.id))?.id ?? null)
const note = useNote(scope, computed(() => (wide.value ? selectedId.value : null)))
// 只认这篇笔记自己的查询结果：读不到（404 / 失权）时清掉详情，不留上一篇或旧缓存
const noteGone = computed(() => gone(note.error.value))
const detail = computed<Note | null>(() => (note.data.value && note.data.value.id === selectedId.value && !noteGone.value ? note.data.value : null))
watch([selectedId, () => note.error.value], ([id]) => {
  const state = id ? cache.getQueryState(kbKey(auth.user?.id, scope, 'note', id)) : undefined
  if (id && state?.status === 'error' && gone(state.error) && !lost.value.includes(id)) lost.value = [...lost.value, id]
}, { flush: 'post' })
function select(id: string) { picked.value = id }
function selectSpace(id: string | null) { spaceId.value = id }

const archived = computed(() => new Set(spaceItems.value.filter(s => s.archived).map(s => s.id)))
const selectedSummary = computed(() => recent.value.find(n => n.id === selectedId.value))
const selectedTitle = computed(() => detail.value?.title ?? selectedSummary.value?.title)
const editable = computed(() => !!detail.value && canEdit.value && !detail.value.deleted_at && detail.value.type !== 'correction')
// 关联笔记：链出与被链合在一起，按 ID 去重（在回收站的不算）
const related = computed(() => {
  const n = detail.value
  if (!n) return []
  const map = new Map<string, { id: string; title: string; space: string; out: boolean; back: boolean }>()
  for (const l of n.links) if (l.note_id && !l.deleted && l.note_id !== n.id) map.set(l.note_id, { id: l.note_id, title: l.title ?? l.text, space: l.space_name ?? '', out: true, back: false })
  for (const b of n.backlinks) {
    if (b.note_id === n.id) continue
    const found = map.get(b.note_id)
    if (found) found.back = true
    else map.set(b.note_id, { id: b.note_id, title: b.title, space: b.space_name, out: false, back: true })
  }
  return [...map.values()]
})
const direction = (r: { out: boolean; back: boolean }) => (r.out && r.back ? '互相链接' : r.out ? '链出' : '被链')

function open(node: GraphNode) {
  if (node.kind === 'file') { window.open(nodeHref(node), '_blank', 'noopener'); return }
  void router.push(nodeHref(node))
}
const sideQuery = ref('')
function sideSearch() { if (sideQuery.value.trim()) void router.push({ path: '/knowledge/notes', query: { q: sideQuery.value.trim() } }) }
const newNoteLink = computed(() => (spaceId.value ? { path: '/knowledge/notes/new', query: { space: spaceId.value } } : '/knowledge/notes/new'))
</script>

<template>
  <div class="kb-view is-mobile-main kb-ov-view">
    <!-- 左栏：知识库导航（点一个只统计它） -->
    <aside class="kb-side" aria-label="知识库分组">
      <div class="kb-head">
        <h2 class="kb-title">知识库</h2>
        <RouterLink v-if="canEdit" :to="newNoteLink" class="kb-icon-btn" aria-label="新建笔记" title="新建笔记"><SquarePen aria-hidden="true" /></RouterLink>
      </div>
      <form class="kb-search" role="search" @submit.prevent="sideSearch">
        <Search aria-hidden="true" />
        <label class="kb-sr" for="kb-ov-search">搜索笔记</label>
        <input id="kb-ov-search" v-model="sideQuery" type="search" placeholder="搜索笔记、标签">
      </form>
      <p v-if="spaces.isPending.value" class="kb-meta" role="status">正在加载知识库…</p>
      <p v-else-if="spaces.error.value" class="kb-error" role="alert">{{ kbError(spaces.error.value) }}</p>
      <KbSpaceList v-else :spaces="spaceItems" :focus="spaceId" @focus="selectSpace" />
      <div class="kb-divider kb-ov-side-links">
        <RouterLink to="/knowledge/docs" class="kb-list-item"><BookMarked aria-hidden="true" /><span class="kb-grow">外部文档（AFFiNE 收藏）</span></RouterLink>
        <RouterLink :to="{ path: '/knowledge/changes', query: { tab: 'trash' } }" class="kb-list-item"><Trash2 aria-hidden="true" /><span class="kb-grow">回收站</span></RouterLink>
      </div>
    </aside>

    <main class="kb-main">
      <KbMobileHeader title="知识库" back="/">
        <RouterLink to="/knowledge/notes" class="kb-icon-btn" aria-label="搜索笔记"><Search aria-hidden="true" /></RouterLink>
        <RouterLink v-if="canEdit" :to="newNoteLink" class="kb-icon-btn" aria-label="新建笔记"><SquarePen aria-hidden="true" /></RouterLink>
      </KbMobileHeader>
      <KbTabs />
      <div class="kb-ov">
        <header class="kb-ov-head">
          <div class="kb-stack">
            <h1 class="kb-desktop-only">知识总览</h1>
            <p class="kb-meta kb-desktop-only">连接笔记、项目与智能体的工作知识。</p>
            <p class="kb-ov-scope">
              <span class="kb-chip"><span v-if="currentSpace" class="kb-dot is-small" :class="`k-${currentSpace.kind}`" aria-hidden="true" />范围：{{ scopeLabel }}</span>
              <button v-if="spaceId" type="button" class="kb-btn is-small" @click="selectSpace(null)"><X aria-hidden="true" />看全部知识库</button>
              <RouterLink v-if="currentSpace" :to="spaceLink(currentSpace)" class="kb-btn is-small">知识库主页<ArrowUpRight aria-hidden="true" /></RouterLink>
            </p>
          </div>
          <RouterLink v-if="canEdit" :to="newNoteLink" class="kb-btn is-primary kb-desktop-only"><Plus aria-hidden="true" />新建笔记</RouterLink>
        </header>

        <KbOverviewStats :summary="summary" :generated-at="failed ? undefined : overview.data.value?.generated_at" :loading="overview.isPending.value" :failed="failed" :scope-label="scopeLabel" @retry="overview.refetch()" />

        <div v-if="wide" class="kb-ov-grid">
          <KbOverviewPreview :scope="scope" :note-id="failed ? null : selectedId" :note-title="selectedTitle" :total="summary?.note_count" :scope-label="scopeLabel" :spaces="spaceItems" @select="select" @open="open" />

          <!-- 所选笔记：真实的作者、时间、标签、摘要与关联；按钮按实际权限 -->
          <section class="kb-card kb-ov-detail" aria-label="所选笔记" aria-live="polite">
            <template v-if="!selectedId">
              <p class="kb-meta">{{ overview.isPending.value ? '正在读取…' : failed ? '统计没有读到，暂时没有可显示的笔记。' : '这个范围里还没有笔记。' }}</p>
              <RouterLink v-if="canEdit && !overview.isPending.value && !failed" :to="newNoteLink" class="kb-btn is-primary"><Plus aria-hidden="true" />新建笔记</RouterLink>
            </template>
            <p v-else-if="noteGone" class="kb-notice" role="status"><CircleAlert aria-hidden="true" /><span class="kb-grow">这篇笔记已删除或你没有权限查看。</span></p>
            <p v-else-if="note.error.value" class="kb-error" role="alert">
              笔记没有读到：{{ kbError(note.error.value) }}
              <button type="button" class="kb-btn is-small" @click="note.refetch()"><RefreshCw aria-hidden="true" />重试</button>
            </p>
            <p v-else-if="!detail" class="kb-meta" role="status">正在读取《{{ selectedTitle }}》…</p>
            <template v-else>
              <div class="kb-ov-detail-head">
                <FileText aria-hidden="true" />
                <h2>{{ detail.title }}</h2>
              </div>
              <div class="kb-ov-chips">
                <span class="kb-chip"><span class="kb-dot is-small" :class="`k-${detail.space_kind}`" aria-hidden="true" />{{ SPACE_KIND_LABELS[detail.space_kind] }} · {{ detail.space_name }}</span>
                <span v-if="archived.has(detail.space_id)" class="kb-badge"><Archive aria-hidden="true" />已归档</span>
                <span class="kb-chip">{{ NOTE_TYPE_LABELS[detail.type] }}</span>
                <span v-for="t in detail.tags.slice(0, 4)" :key="t" class="kb-tag">#{{ t }}</span>
              </div>
              <dl class="kb-dl kb-ov-facts">
                <dt>更新者</dt><dd>{{ authorLabel(detail.updated_by) }}</dd>
                <dt>更新时间</dt><dd><time :datetime="detail.updated_at">{{ when(detail.updated_at) }}</time> · 第 {{ detail.version }} 版</dd>
                <dt>状态</dt><dd>{{ statusOf(detail).label }}</dd>
              </dl>
              <p v-if="excerpt(detail.body)" class="kb-ov-excerpt">{{ excerpt(detail.body, 160) }}</p>
              <p v-else class="kb-meta">正文是空的。</p>
              <div class="kb-side-section">
                <div class="kb-side-head"><h3 class="kb-ov-subtitle">关联笔记</h3><span class="kb-meta kb-num">共 {{ related.length }} 篇</span></div>
                <ul v-if="related.length" class="kb-list">
                  <li v-for="r in related.slice(0, 6)" :key="r.id">
                    <button type="button" class="kb-list-item" @click="select(r.id)">
                      <FileText aria-hidden="true" class="kb-ov-row-icon" />
                      <span class="kb-grow">{{ r.title }}</span><span class="kb-meta">{{ direction(r) }}{{ r.space ? ' · ' + r.space : '' }}</span>
                    </button>
                  </li>
                </ul>
                <p v-else class="kb-meta">还没有和别的笔记互相链接。</p>
              </div>
              <div class="kb-ov-actions">
                <RouterLink :to="`/knowledge/notes/${detail.id}`" class="kb-btn is-primary">打开笔记</RouterLink>
                <RouterLink v-if="editable" :to="`/knowledge/notes/${detail.id}/edit`" class="kb-btn"><SquarePen aria-hidden="true" />编辑</RouterLink>
              </div>
            </template>
          </section>
        </div>

        <!-- 手机：知识库列表（原来 /knowledge 的手机首页） -->
        <section class="kb-card kb-mobile-only kb-ov-mobile-spaces" aria-label="知识库列表">
          <h2 class="kb-ov-subtitle">知识库</h2>
          <KbSpaceList v-if="spaceItems.length" :spaces="spaceItems" links />
          <RouterLink to="/knowledge/docs" class="kb-recent"><span>外部文档</span><span class="kb-meta">AFFiNE 文档收藏</span></RouterLink>
        </section>

        <!-- 最近更新：按笔记去重，updated_at 倒序（服务端给出） -->
        <section class="kb-card kb-ov-recent" aria-labelledby="kb-ov-recent-title">
          <div class="kb-side-head">
            <h2 id="kb-ov-recent-title" class="kb-ov-subtitle">最近更新</h2>
            <RouterLink :to="spaceId ? { path: '/knowledge/notes', query: { space: spaceId } } : '/knowledge/notes'" class="kb-meta">全部笔记</RouterLink>
          </div>
          <p v-if="overview.isPending.value" class="kb-meta" role="status">正在读取…</p>
          <p v-else-if="failed" class="kb-error" role="alert">
            最近更新没有读到：{{ kbError(overview.error.value) }}
            <button type="button" class="kb-btn is-small" @click="overview.refetch()"><RefreshCw aria-hidden="true" />重试</button>
          </p>
          <p v-else-if="!recent.length" class="kb-empty">这个范围里还没有笔记。</p>
          <table v-else class="kb-ov-table">
            <caption class="kb-sr">{{ scopeLabel }}最近更新的笔记</caption>
            <thead><tr><th scope="col">笔记</th><th scope="col" class="kb-ov-col-space">知识库</th><th scope="col" class="kb-ov-col-author">更新者</th><th scope="col" class="kb-ov-col-time">更新时间</th></tr></thead>
            <tbody>
              <tr v-for="n in recent" :key="n.id" :class="{ 'is-selected': wide && n.id === selectedId }">
                <td>
                  <span class="kb-ov-title-cell">
                    <FileText aria-hidden="true" class="kb-ov-row-icon" />
                    <button v-if="wide" type="button" class="kb-ov-link" :aria-pressed="n.id === selectedId" :title="`在总览里查看《${n.title}》`" @click="select(n.id)">{{ n.title }}</button>
                    <RouterLink v-else :to="`/knowledge/notes/${n.id}`" class="kb-ov-link">{{ n.title }}</RouterLink>
                    <RouterLink v-if="wide" :to="`/knowledge/notes/${n.id}`" class="kb-icon-btn kb-ov-open" :aria-label="`打开《${n.title}》`" :title="`打开《${n.title}》`"><ArrowUpRight aria-hidden="true" /></RouterLink>
                  </span>
                  <span class="kb-meta kb-ov-row-meta">{{ n.space_name }}{{ archived.has(n.space_id) ? '（已归档）' : '' }} · {{ authorLabel(n.updated_by) }} · {{ when(n.updated_at) }}</span>
                </td>
                <td class="kb-ov-col-space"><span class="kb-chip"><span class="kb-dot is-small" :class="`k-${n.space_kind}`" aria-hidden="true" />{{ n.space_name }}{{ archived.has(n.space_id) ? ' · 已归档' : '' }}</span></td>
                <td class="kb-ov-col-author">{{ authorLabel(n.updated_by) }}</td>
                <td class="kb-ov-col-time"><time :datetime="n.updated_at">{{ when(n.updated_at) }}</time></td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>
    </main>
  </div>
</template>
