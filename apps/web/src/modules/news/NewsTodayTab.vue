<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import DataState from '../../components/DataState.vue'
import { unique } from '../../features/agents/queries'
import NewsBriefingView from './NewsBriefingView.vue'
import NewsItems from './NewsItems.vue'
import NewsReadingSidebar from './NewsReadingSidebar.vue'
import { useNewsBriefings, useNewsToday } from './api'

// 今天（T-0174 阅读页）：左侧最新一份早报 + 相关资讯（每个兴趣主题今天的条目，标了“不感兴趣”的不在这里），
// 右侧关注主题、当前已加载的概况与往期入口。TrendRadar 连不上只影响“相关资讯”，已保存的早报照常显示。
const auth = useAuthStore(), scope = auth.workspaceId!
const today = useNewsToday(scope)
// 与“往期”标签同一个查询（同一个键），切过去不再重新读
const briefings = useNewsBriefings(scope)
const data = computed(() => today.data.value)
const archive = computed(() => unique(briefings.data.value?.pages))
const notEnabled = computed(() => data.value?.message === '早报没有启用')
const topics = computed(() => data.value?.topics ?? [])
const total = computed(() => new Set(topics.value.flatMap((topic) => topic.items.map((item) => item.url))).size)
// 按主题筛选相关资讯；刷新后主题没了就回到全部
const filter = ref<string | null>(null)
const active = computed(() => (topics.value.some((topic) => topic.name === filter.value) ? filter.value : null))
const shown = computed(() => (active.value ? topics.value.filter((topic) => topic.name === active.value) : topics.value))
</script>

<template>
  <div class="news-today">
    <div class="news-reading-col">
      <DataState :loading="today.isPending.value" :error="today.error.value" :empty="false" empty-text="" :stale="!!data" @retry="today.refetch()" />
      <template v-if="data">
        <p v-if="notEnabled" role="status" class="feedback news-banner">早报还没有启用：owner 可以在“设置 → 新闻早报”里打开。</p>
        <NewsBriefingView v-if="data.briefing" :briefing="data.briefing" />
        <section v-else class="ws-panel news-reading" aria-label="今天的早报">
          <h2>今天还没有早报</h2>
          <p class="card-state">到了推送时间会自动送达，也可以先看看热榜或往期早报。</p>
        </section>
        <section class="ws-panel news-related" aria-label="相关资讯">
          <div class="news-related-head">
            <h2>相关资讯</h2>
            <span v-if="topics.length && !data.unavailable" class="news-count">当前已加载 {{ total }} 条</span>
          </div>
          <div v-if="topics.length > 1" class="ws-chips news-topic-filter" role="group" aria-label="按主题筛选相关资讯">
            <button type="button" class="ws-chip" :aria-pressed="!active" @click="filter = null">全部<span class="ws-chip-count">{{ total }}</span></button>
            <button v-for="topic in topics" :key="topic.name" type="button" class="ws-chip" :aria-pressed="active === topic.name" @click="filter = topic.name">{{ topic.name }}<span class="ws-chip-count">{{ topic.items.length }}</span></button>
          </div>
          <p v-if="data.message && !notEnabled" role="status" class="feedback news-banner">{{ data.message }}</p>
          <p v-else-if="data.unavailable" role="status" class="feedback news-banner">TrendRadar 暂时连不上：早报照常显示，各主题的条目稍后再看。</p>
          <section v-for="topic in shown" :key="topic.name" class="news-topic" :aria-label="`主题：${topic.name}`">
            <h3>{{ topic.name }} <span class="news-count">{{ topic.items.length }} 条</span></h3>
            <NewsItems :items="topic.items" empty-text="今天这个主题还没有条目。" hide-topic />
          </section>
          <p v-if="!topics.length && !data.unavailable && !data.message" class="card-state">还没有启用的兴趣主题：今天没有可以列出的相关资讯。</p>
        </section>
      </template>
    </div>
    <NewsReadingSidebar
      :today="data" :loading="today.isPending.value"
      :archive="archive" :archive-loading="briefings.isPending.value" :archive-error="briefings.error.value"
      @retry-archive="briefings.refetch()"
    />
  </div>
</template>
