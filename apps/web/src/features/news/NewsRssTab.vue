<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import DataState from '../../components/DataState.vue'
import NewsItems from './NewsItems.vue'
import { useNewsRss } from './api'

// RSS：最近几天（1~7 天）的订阅条目
const auth = useAuthStore()
const days = ref(1)
const rss = useNewsRss(auth.workspaceId!, days)
</script>

<template>
  <section class="panel news-card" aria-label="RSS">
    <form class="news-filters" @submit.prevent="rss.refetch()">
      <label class="form-field"><span>时间范围</span><select v-model.number="days"><option v-for="d in 7" :key="d" :value="d">{{ d === 1 ? '今天' : `最近 ${d} 天` }}</option></select></label>
      <button type="submit" class="secondary-button" :disabled="rss.isFetching.value">{{ rss.isFetching.value ? '正在刷新…' : '刷新' }}</button>
    </form>
    <DataState :loading="rss.isPending.value" :error="rss.error.value" :empty="false" empty-text="" :stale="!!rss.data.value" @retry="rss.refetch()" />
    <NewsItems v-if="rss.data.value" :items="rss.data.value.items" :message="rss.data.value.message" :unavailable="rss.data.value.unavailable" empty-text="这段时间没有 RSS 条目。RSS 源在“设置 → 新闻早报”里添加。" label="RSS 条目" />
  </section>
</template>
