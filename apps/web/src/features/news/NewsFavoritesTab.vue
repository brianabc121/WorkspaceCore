<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import DataState from '../../components/DataState.vue'
import { safeUrl } from '../../widgets/registry'
import { unique } from '../agents/queries'
import { formatTime } from '../time'
import { newsError, useNewsActions, useNewsFavorites, type NewsFeedback } from './api'

// 收藏：我收藏过的条目（只看得到自己的），可以取消收藏
const auth = useAuthStore(), scope = auth.workspaceId!
const list = useNewsFavorites(scope), actions = useNewsActions(scope)
const items = computed(() => unique(list.data.value?.pages))
const canFeedback = computed(() => ['owner', 'admin', 'member'].includes(auth.currentWorkspace?.role ?? ''))
const busy = ref<string | null>(null), error = ref('')
async function remove(item: NewsFeedback) {
  if (busy.value) return
  busy.value = item.id; error.value = ''
  try { await actions.remove(item.id) } catch (cause) { error.value = newsError(cause) } finally { busy.value = null }
}
</script>

<template>
  <section class="panel news-card" aria-label="收藏">
    <p v-if="error" role="alert" class="feedback news-banner">{{ error }}</p>
    <DataState :loading="list.isPending.value" :error="list.error.value" :empty="!items.length" empty-text="还没有收藏：在今天、热榜、RSS 或搜索里点“收藏”。" :stale="!!list.data.value" @retry="list.refetch()" />
    <ul v-if="items.length" class="news-items" aria-label="收藏的条目">
      <li v-for="item in items" :key="item.id" class="news-item" :aria-label="item.title">
        <div class="news-item-main">
          <a v-if="safeUrl(item.url)" class="news-item-title" :href="item.url" target="_blank" rel="noopener noreferrer">{{ item.title }}</a>
          <span v-else class="news-item-title">{{ item.title }}</span>
          <p class="news-meta">
            <span v-if="item.source_name">{{ item.source_name }}</span>
            <span>{{ item.source_type === 'rss' ? 'RSS' : '热榜' }}</span>
            <span v-if="item.topic" class="chip">{{ item.topic }}</span>
            <span>收藏于 {{ formatTime(item.created_at) }}</span>
          </p>
        </div>
        <div v-if="canFeedback" class="news-item-actions">
          <button type="button" class="secondary-button" :disabled="!!busy" @click="remove(item)">取消收藏</button>
        </div>
      </li>
    </ul>
    <button v-if="list.hasNextPage.value" type="button" class="secondary-button news-more" :disabled="list.isFetchingNextPage.value" @click="list.fetchNextPage()">{{ list.isFetchingNextPage.value ? '正在加载…' : '加载更多' }}</button>
  </section>
</template>
