<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import DataState from '../../components/DataState.vue'
import NewsItems from './NewsItems.vue'
import { useNewsSearch, type SearchParams } from './api'

// 搜索：按关键词搜热榜（可含 RSS），点“搜索”才查
const auth = useAuthStore()
const q = ref(''), days = ref(7), includeRss = ref(true)
const params = ref<SearchParams | null>(null)
const result = useNewsSearch(auth.workspaceId!, params)
const DAYS = [1, 3, 7, 14, 30]
function search() {
  const text = q.value.trim().slice(0, 100)
  if (!text) { params.value = null; return }
  const next = { q: text, days: days.value, include_rss: includeRss.value }
  if (JSON.stringify(next) === JSON.stringify(params.value)) void result.refetch()
  else params.value = next
}
</script>

<template>
  <section class="panel news-card" aria-label="搜索">
    <form class="news-filters" role="search" aria-label="搜索新闻" @submit.prevent="search">
      <label class="form-field"><span>关键词</span><input v-model="q" type="search" maxlength="100" placeholder="例如：大模型" /></label>
      <label class="form-field"><span>时间范围</span><select v-model.number="days"><option v-for="d in DAYS" :key="d" :value="d">{{ d === 1 ? '今天' : `最近 ${d} 天` }}</option></select></label>
      <label class="check-field"><input v-model="includeRss" type="checkbox" />包含 RSS</label>
      <button type="submit" class="primary-button" :disabled="!q.trim() || result.isFetching.value">{{ result.isFetching.value ? '正在搜索…' : '搜索' }}</button>
    </form>
    <p v-if="!params" class="card-state">输入关键词后点“搜索”。</p>
    <template v-else>
      <DataState :loading="result.isPending.value" :error="result.error.value" :empty="false" empty-text="" :stale="!!result.data.value" @retry="result.refetch()" />
      <NewsItems v-if="result.data.value" :items="result.data.value.items" :message="result.data.value.message" :unavailable="result.data.value.unavailable" :empty-text="`没有找到“${params.q}”相关的新闻。`" label="搜索结果" />
    </template>
  </section>
</template>
