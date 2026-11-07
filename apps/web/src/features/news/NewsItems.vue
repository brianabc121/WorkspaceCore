<script setup lang="ts">
import { computed } from 'vue'
import NewsItemRow from './NewsItemRow.vue'
import type { NewsItem } from './api'

// 一组条目，连同 TrendRadar 的说明（没有数据、暂时连不上）
const props = defineProps<{ items: NewsItem[]; emptyText: string; message?: string | null; unavailable?: boolean; hideTopic?: boolean; label?: string }>()
// 同一个链接可能出现在两个平台上：键里带来源，仍然重复时加序号
const keyed = computed(() => {
  const seen = new Map<string, number>()
  return props.items.map((item) => {
    const base = `${item.source_type}:${item.source_id}:${item.url}`
    const count = seen.get(base) ?? 0
    seen.set(base, count + 1)
    return { item, key: count ? `${base}#${count}` : base }
  })
})
</script>

<template>
  <p v-if="unavailable" role="status" class="feedback news-banner">TrendRadar 暂时连不上，稍后再看。</p>
  <p v-if="message && !(unavailable && /连不上/.test(message))" role="status" class="feedback news-banner">{{ message }}</p>
  <ul v-if="items.length" class="news-items" :aria-label="label">
    <NewsItemRow v-for="entry in keyed" :key="entry.key" :item="entry.item" :hide-topic="hideTopic" />
  </ul>
  <p v-else-if="!unavailable" class="card-state">{{ emptyText }}</p>
</template>
