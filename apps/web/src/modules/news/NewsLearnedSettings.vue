<script setup lang="ts">
import { ArrowRight, Sparkles } from '@lucide/vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import { formatTime } from '../../features/time'
import type { Schema } from '../../services/api'
import type { SettingsTab } from './settingsState'

// “学习偏好”标签（T-0195）：最近 60 天的收藏 / 不感兴趣算出的主题分数与建议关掉的来源（服务端每天计算）。
// 只展示：建议不会自动执行，按钮只是跳到“新闻来源”标签，由 owner 自己决定。
defineProps<{ learned: Schema['NewsLearnedOut'] }>()
const emit = defineEmits<{ tab: [SettingsTab] }>()
const sourceName = (type: string) => (type === 'rss' ? 'RSS' : '热榜')
const signed = (n: number) => (n > 0 ? `+${n}` : String(n))
const adjusted = (t: Schema['NewsTopicLearned']) => `${t.adjusted_max_items || '不限'}${t.adjusted_max_items > t.max_items && t.max_items ? '（上调）' : t.adjusted_max_items < t.max_items ? '（下调）' : ''}`
</script>

<template>
  <ManagementSection title="学到的偏好" :description="`最近一次计算：${formatTime(learned.computed_at)}`">
    <template #title><span class="nws-title"><Sparkles aria-hidden="true" />学到的偏好</span></template>
    <p class="form-help">按最近 60 天的“收藏”和“不感兴趣”计算：每个主题的分数 = 收藏数 − 不感兴趣数。分数高的主题排在前面、每天多推几条（最多 +10），分数低的少推（最少 3 条）。每天 03:10 重新计算一次。</p>
    <p v-if="!learned.topics.length" class="asv-sub">还没有可以调整的主题。</p>
    <div v-else class="ws-table-wrap">
      <table class="ws-table asv-table nws-learned">
        <thead><tr><th scope="col">主题</th><th scope="col" class="is-num">分数</th><th scope="col" class="is-num">设置的条数</th><th scope="col" class="is-num">实际推送条数</th></tr></thead>
        <tbody>
          <tr v-for="t in learned.topics" :key="t.name">
            <th scope="row">{{ t.name }}</th>
            <td class="is-num" data-label="分数">{{ signed(t.score) }}</td>
            <td class="is-num" data-label="设置的条数">{{ t.max_items || '不限' }}</td>
            <td class="is-num" data-label="实际推送条数">{{ adjusted(t) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <template v-if="learned.source_suggestions.length">
      <h3 class="nws-subhead">可以考虑关掉的来源</h3>
      <ul class="nws-suggestions">
        <li v-for="s in learned.source_suggestions" :key="`${s.source_type}:${s.source_id}`">「{{ s.source_name || s.source_id }}」（{{ sourceName(s.source_type) }}）被标了 {{ s.dismisses }} 次不感兴趣、{{ s.favorites }} 次收藏：可以在“新闻来源”里关掉它（不会自动关）。</li>
      </ul>
      <button type="button" class="asv-link" @click="emit('tab', 'sources')">去“新闻来源”查看<ArrowRight aria-hidden="true" /></button>
    </template>
  </ManagementSection>
</template>
