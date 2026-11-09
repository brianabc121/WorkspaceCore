<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight, Check, Eye, Info, Layers, Pencil, Rss, Tag } from '@lucide/vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import type { Schema } from '../../services/api'
import type { NewsForm } from './settingsForm'
import type { SettingsTab } from './settingsState'

// “基本与时间”标签下方的来源与兴趣概览（T-0195，效果图 06）：按当前草稿显示（含没保存的修改），
// 只负责跳到对应标签，不在这里改设置。
const props = defineProps<{ form: NewsForm; choices: readonly Schema['NewsPlatformChoice'][]; canEdit: boolean }>()
const emit = defineEmits<{ tab: [SettingsTab]; feed: [number] }>()
const platforms = computed(() => props.choices.filter(p => props.form.platforms.includes(p.id)).map(p => p.name))
const feedName = (name: string, index: number) => name.trim() || `第 ${index + 1} 个`
</script>

<template>
  <ManagementSection title="新闻来源">
    <template #title><span class="nws-title"><Layers aria-hidden="true" />新闻来源</span></template>
    <template #actions><button type="button" class="asv-link" @click="emit('tab', 'sources')">管理全部来源<ArrowRight aria-hidden="true" /></button></template>
    <div class="nws-sum-hot">
      <span class="nws-check-mark" :class="{ 'is-on': platforms.length }"><Check v-if="platforms.length" aria-hidden="true" /></span>
      <strong>热榜</strong>
      <span class="asv-sub">{{ platforms.length ? platforms.join('、') : '没有选热榜平台' }}</span>
    </div>
    <ul v-if="form.feeds.length" class="nws-sum-feeds" aria-label="RSS 源概览">
      <li v-for="(feed, index) in form.feeds" :key="index">
        <Rss aria-hidden="true" />
        <span class="nws-sum-name">{{ feedName(feed.name, index) }}</span>
        <span class="asv-state" :class="feed.enabled ? 'tone-green' : 'tone-muted'">{{ feed.enabled ? '已启用' : '已停用' }}</span>
        <button type="button" class="asv-link" :aria-label="`${canEdit ? '编辑' : '查看'} RSS 源：${feedName(feed.name, index)}`" @click="emit('feed', index)"><component :is="canEdit ? Pencil : Eye" aria-hidden="true" />{{ canEdit ? '编辑' : '查看' }}</button>
      </li>
    </ul>
    <p v-else class="asv-sub">还没有 RSS 源。</p>
  </ManagementSection>

  <ManagementSection title="兴趣主题">
    <template #title><span class="nws-title"><Tag aria-hidden="true" />兴趣主题</span></template>
    <template #actions><button type="button" class="asv-link" @click="emit('tab', 'topics')">管理关键词与排除词<ArrowRight aria-hidden="true" /></button></template>
    <ul v-if="form.topics.length" class="nws-sum-topics" aria-label="兴趣主题概览">
      <li v-for="(topic, index) in form.topics" :key="index" :class="{ 'is-off': !topic.enabled }">
        <span class="nws-sum-icon"><Tag aria-hidden="true" /></span>
        <span class="nws-sum-text"><strong>{{ topic.name.trim() || `第 ${index + 1} 个主题` }}</strong>
          <span class="asv-sub">{{ Number(topic.max_items) ? `每天最多 ${topic.max_items} 条` : '每天不限条数' }}{{ topic.enabled ? '' : ' · 已停用' }}</span></span>
        <button type="button" class="nws-icon-button" :aria-label="`${canEdit ? '编辑' : '查看'}兴趣主题：${topic.name.trim() || index + 1}`" @click="emit('tab', 'topics')"><component :is="canEdit ? Pencil : Eye" aria-hidden="true" /></button>
      </li>
    </ul>
    <p v-else class="asv-sub">还没有兴趣主题。</p>
    <p class="mg-hint"><Info aria-hidden="true" /><span>收藏与“不感兴趣”会影响后续主题偏好。<button type="button" class="asv-link" @click="emit('tab', 'learned')">查看学习偏好<ArrowRight aria-hidden="true" /></button></span></p>
  </ManagementSection>
</template>
