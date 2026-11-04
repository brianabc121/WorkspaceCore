<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { CircleAlert, Pin, Search, SquarePen, X } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { EDITABLE_TYPES, NOTE_TYPE_LABELS, SPACE_KIND_LABELS, kbError, useNotes, useSpaces, type NoteFilters, type NoteType } from './api'
import { when } from './format'
import KbMobileHeader from './KbMobileHeader.vue'
import KbTabs from './KbTabs.vue'

// 笔记列表与搜索（T-0099）：按知识库、类型、标签、作者筛选；搜索标题、别名、标签和正文。
// 地址里的参数（?q= ?space= ?type= ?tag= ?author=）就是筛选条件，可以收藏或分享。
const auth = useAuthStore(), route = useRoute(), router = useRouter(), scope = auth.workspaceId!
const canEdit = computed(() => ['owner', 'admin', 'member'].includes(auth.currentWorkspace?.role ?? ''))
const spaces = useSpaces(scope)
const str = (v: unknown) => typeof v === 'string' ? v : ''
const filters = computed<NoteFilters>(() => ({
  q: str(route.query.q) || undefined,
  space_id: str(route.query.space) || undefined,
  type: (str(route.query.type) || undefined) as NoteType | undefined,
  tag: str(route.query.tag) || undefined,
  author: (['user', 'agent'].includes(str(route.query.author)) ? str(route.query.author) : undefined) as 'user' | 'agent' | undefined,
}))
const notes = useNotes(scope, filters)
const items = computed(() => notes.data.value?.pages.flatMap(p => p.items) ?? [])
const text = ref(str(route.query.q))
watch(() => route.query.q, (q) => { text.value = str(q) })
function set(key: string, value: string | undefined) {
  const query = { ...route.query }
  if (value) query[key] = value; else delete query[key]
  void router.replace({ query })
}
const spaceName = (id: string | undefined) => spaces.data.value?.items.find(s => s.id === id)?.name ?? ''
const newHref = computed(() => filters.value.space_id ? `/knowledge/notes/new?space=${filters.value.space_id}` : '/knowledge/notes/new')
</script>

<template>
  <div class="kb-view is-mobile-main">
    <main class="kb-main">
      <KbMobileHeader title="笔记" back="/knowledge" back-label="返回知识库">
        <RouterLink v-if="canEdit" :to="newHref" class="kb-icon-btn" aria-label="新建笔记"><SquarePen aria-hidden="true" /></RouterLink>
      </KbMobileHeader>
      <KbTabs />
      <div class="kb-page" style="max-width: 920px">
        <div class="kb-hero kb-desktop-only">
          <div><h1>笔记</h1><p class="kb-meta" style="margin: 4px 0 0">所有知识库里的笔记。置顶的排在前面。</p></div>
          <RouterLink v-if="canEdit" :to="newHref" class="kb-btn is-primary"><SquarePen aria-hidden="true" />新建笔记</RouterLink>
        </div>
        <form class="kb-search" role="search" style="height: 42px; background: var(--kb-surface)" @submit.prevent="set('q', text.trim() || undefined)">
          <Search aria-hidden="true" />
          <label class="kb-sr" for="kb-notes-search">搜索笔记</label>
          <input id="kb-notes-search" v-model="text" type="search" placeholder="搜索标题、别名、标签或正文，回车搜索" maxlength="100" style="font-size: 14px">
        </form>
        <div style="display: flex; flex-wrap: wrap; gap: 10px; align-items: center">
          <label class="kb-check" style="color: var(--kb-muted)">知识库
            <select class="kb-select" :value="filters.space_id ?? ''" @change="set('space', ($event.target as HTMLSelectElement).value || undefined)">
              <option value="">全部</option>
              <option v-for="s in spaces.data.value?.items ?? []" :key="s.id" :value="s.id">{{ SPACE_KIND_LABELS[s.kind] }} · {{ s.name }}</option>
            </select>
          </label>
          <label class="kb-check" style="color: var(--kb-muted)">类型
            <select class="kb-select" :value="filters.type ?? ''" @change="set('type', ($event.target as HTMLSelectElement).value || undefined)">
              <option value="">全部</option>
              <option v-for="t in [...EDITABLE_TYPES, 'correction' as const]" :key="t" :value="t">{{ NOTE_TYPE_LABELS[t] }}</option>
            </select>
          </label>
          <label class="kb-check" style="color: var(--kb-muted)">最后修改
            <select class="kb-select" :value="filters.author ?? ''" @change="set('author', ($event.target as HTMLSelectElement).value || undefined)">
              <option value="">人和智能体</option><option value="user">人</option><option value="agent">智能体</option>
            </select>
          </label>
          <span v-if="filters.tag" class="kb-tag">#{{ filters.tag }}<button type="button" class="kb-tab-close" style="width: 18px; height: 18px" aria-label="去掉标签筛选" @click="set('tag', undefined)"><X aria-hidden="true" /></button></span>
        </div>
        <p v-if="notes.isPending.value" class="kb-meta" role="status">正在加载…</p>
        <p v-else-if="notes.error.value" class="kb-error" role="alert">{{ kbError(notes.error.value) }}</p>
        <template v-else>
          <p class="kb-meta" role="status" style="margin: 0">{{ items.length }}{{ notes.hasNextPage.value ? '+' : '' }} 篇{{ filters.q ? `，搜索“${filters.q}”` : '' }}{{ filters.space_id ? `，在${spaceName(filters.space_id)}里` : '' }}</p>
          <ul class="kb-list" style="gap: 6px">
            <li v-for="n in items" :key="n.id">
              <RouterLink :to="`/knowledge/notes/${n.id}`" class="kb-card" style="padding: 12px 14px; gap: 4px; text-decoration: none; color: var(--kb-text)">
                <span style="display: flex; align-items: center; gap: 8px">
                  <strong style="flex: 1; min-width: 0">{{ n.title }}</strong>
                  <Pin v-if="n.pinned" aria-label="置顶" style="width: 14px; height: 14px; color: var(--kb-muted)" />
                  <span v-if="n.conflict" class="kb-badge is-warning"><CircleAlert aria-hidden="true" />有冲突</span>
                </span>
                <span class="kb-meta" style="display: flex; flex-wrap: wrap; align-items: center; gap: 6px">
                  <span class="kb-dot is-small" :class="`k-${n.space_kind}`" aria-hidden="true" />{{ n.space_name }} · {{ NOTE_TYPE_LABELS[n.type] }} · {{ n.updated_by.name }}{{ n.updated_by.kind === 'agent' ? '（智能体）' : '' }} {{ when(n.updated_at) }}
                  <span v-for="t in n.tags.slice(0, 4)" :key="t">#{{ t }}</span>
                </span>
              </RouterLink>
            </li>
          </ul>
          <p v-if="!items.length" class="kb-empty">没有找到笔记。<RouterLink v-if="canEdit" :to="newHref">新建一篇</RouterLink></p>
          <button v-if="notes.hasNextPage.value" type="button" class="kb-btn" :disabled="notes.isFetchingNextPage.value" @click="notes.fetchNextPage()">加载更多</button>
        </template>
      </div>
    </main>
  </div>
</template>
