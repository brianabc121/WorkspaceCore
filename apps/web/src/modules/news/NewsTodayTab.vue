<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '../../stores/auth'
import DataState from '../../components/DataState.vue'
import NewsBriefingView from './NewsBriefingView.vue'
import NewsItems from './NewsItems.vue'
import { useNewsToday } from './api'

// 今天：最新一份早报 + 每个兴趣主题今天的条目（标了“不感兴趣”的不在这里）
const auth = useAuthStore()
const today = useNewsToday(auth.workspaceId!)
const data = computed(() => today.data.value)
const emptyMessage = computed(() => data.value?.message === '早报没有启用' ? '早报还没有启用：owner 可以在“设置 → 新闻早报”里打开。' : '')
</script>

<template>
  <div class="news-panel">
    <DataState :loading="today.isPending.value" :error="today.error.value" :empty="false" empty-text="" :stale="!!data" @retry="today.refetch()" />
    <template v-if="data">
      <p v-if="emptyMessage" role="status" class="feedback news-banner">{{ emptyMessage }}</p>
      <p v-else-if="data.message" role="status" class="feedback news-banner">{{ data.message }}</p>
      <p v-if="data.unavailable && !data.message" role="status" class="feedback news-banner">TrendRadar 暂时连不上：早报照常显示，各主题的条目稍后再看。</p>
      <NewsBriefingView v-if="data.briefing" :briefing="data.briefing" />
      <section v-else class="panel news-card" aria-label="今天的早报">
        <h2>今天还没有早报</h2>
        <p class="card-state">到了推送时间会自动送达，也可以先看看热榜或往期早报。</p>
      </section>
      <section v-for="topic in data.topics" :key="topic.name" class="panel news-card" :aria-label="`主题：${topic.name}`">
        <h2>{{ topic.name }} <span class="news-count">{{ topic.items.length }} 条</span></h2>
        <NewsItems :items="topic.items" empty-text="今天这个主题还没有条目。" hide-topic />
      </section>
    </template>
  </div>
</template>
