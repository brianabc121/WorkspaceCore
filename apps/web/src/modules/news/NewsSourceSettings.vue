<script setup lang="ts">
import { useId } from 'vue'
import { Flame, Pencil, Plus, Rss, Trash } from '@lucide/vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import type { Schema } from '../../services/api'
import { MAX_FEEDS, emptyFeed, type FeedDraft, type FormPatch, type NewsForm } from './settingsForm'

// “新闻来源”标签（T-0195）：热榜平台（来自接口的 platform_choices）、自建 NewsNow 地址与 RSS 源。
// RSS 源一行一条，点“编辑”在行内展开；新加的源自动展开。只发出对草稿的修改，不保存。
const props = defineProps<{ form: NewsForm; choices: readonly Schema['NewsPlatformChoice'][]; canEdit: boolean; disabled: boolean }>()
const emit = defineEmits<{ patch: [FormPatch] }>()
const open = defineModel<number | null>('open', { default: null })
const id = useId()
const target = (event: Event) => event.target as HTMLInputElement

function togglePlatform(platform: Schema['NewsPlatformChoice']['id'], on: boolean) {
  const rest = props.form.platforms.filter(p => p !== platform)
  emit('patch', { platforms: on ? [...rest, platform] : rest })
}
function setFeed(index: number, change: Partial<FeedDraft>) {
  emit('patch', { feeds: props.form.feeds.map((f, i) => (i === index ? { ...f, ...change } : f)) })
}
function addFeed() {
  if (props.form.feeds.length >= MAX_FEEDS) return
  emit('patch', { feeds: [...props.form.feeds, emptyFeed()] })
  open.value = props.form.feeds.length
}
function removeFeed(index: number) {
  emit('patch', { feeds: props.form.feeds.filter((_, i) => i !== index) })
  open.value = null
}
const feedName = (feed: FeedDraft, index: number) => feed.name.trim() || `第 ${index + 1} 个`
</script>

<template>
  <ManagementSection title="热榜平台" description="TrendRadar 每 30 分钟抓一次所选平台的热榜；平台列表来自当前部署。">
    <template #title><span class="nws-title"><Flame aria-hidden="true" />热榜平台</span></template>
    <fieldset class="nws-fieldset" :disabled="disabled">
      <div class="nws-checks" role="group" aria-label="热榜平台">
        <label v-for="p in choices" :key="p.id" class="nws-check"><input type="checkbox" :checked="form.platforms.includes(p.id)" @change="togglePlatform(p.id, target($event).checked)" />{{ p.name }}</label>
      </div>
      <label class="form-field nws-wide-field"><span>自建 NewsNow 地址（可不填，留空用公共接口）</span>
        <input :value="form.newsnow_api_url" type="url" maxlength="500" placeholder="https://newsnow.example.com" @input="emit('patch', { newsnow_api_url: target($event).value })" />
      </label>
    </fieldset>
  </ManagementSection>

  <ManagementSection title="RSS 源" description="RSSHub 的订阅也填在这里。ID 用小写字母、数字和连字符（例如 hacker-news）。">
    <template #title><span class="nws-title"><Rss aria-hidden="true" />RSS 源<span class="nws-count">{{ form.feeds.length }} / {{ MAX_FEEDS }}</span></span></template>
    <template v-if="canEdit && form.feeds.length < MAX_FEEDS" #actions>
      <button type="button" class="secondary-button" :disabled="disabled" @click="addFeed"><Plus aria-hidden="true" />添加 RSS 源</button>
    </template>
    <p v-if="!form.feeds.length" class="asv-sub">还没有 RSS 源。</p>
    <ul v-else class="nws-feeds" aria-label="RSS 源">
      <li v-for="(feed, index) in form.feeds" :key="index" class="nws-feed" :class="{ 'is-open': open === index }">
        <div class="nws-feed-row">
          <Rss aria-hidden="true" />
          <div class="nws-feed-main">
            <strong>{{ feedName(feed, index) }}</strong>
            <span class="asv-sub nws-url">{{ feed.url || '还没有填地址' }}</span>
            <span class="asv-sub">ID {{ feed.id || '—' }} · {{ Number(feed.max_age_days) ? `只看最近 ${feed.max_age_days} 天` : '默认只看最近 3 天' }}</span>
          </div>
          <span class="asv-state" :class="feed.enabled ? 'tone-green' : 'tone-muted'">{{ feed.enabled ? '已启用' : '已停用' }}</span>
          <button v-if="canEdit" type="button" class="asv-link" :aria-expanded="open === index" :aria-controls="`${id}-feed-${index}`" :aria-label="`${open === index ? '收起' : '编辑'} RSS 源：${feedName(feed, index)}`" @click="open = open === index ? null : index"><Pencil aria-hidden="true" />{{ open === index ? '收起' : '编辑' }}</button>
        </div>
        <fieldset v-if="canEdit && open === index" :id="`${id}-feed-${index}`" class="nws-fieldset nws-feed-editor" :disabled="disabled" :aria-label="`RSS 源 ${index + 1}`">
          <div class="nws-grid">
            <label class="form-field"><span>ID</span><input :value="feed.id" maxlength="40" placeholder="hacker-news" autocapitalize="off" @input="setFeed(index, { id: target($event).value })" /></label>
            <label class="form-field"><span>名称</span><input :value="feed.name" maxlength="60" placeholder="Hacker News" @input="setFeed(index, { name: target($event).value })" /></label>
            <label class="form-field nws-full"><span>地址</span><input :value="feed.url" type="url" maxlength="500" placeholder="https://hnrss.org/frontpage" @input="setFeed(index, { url: target($event).value })" /></label>
            <label class="form-field"><span>只看最近几天（0 = 默认 3 天）</span><input :value="feed.max_age_days" type="number" min="0" max="30" @input="setFeed(index, { max_age_days: Number(target($event).value) || 0 })" /></label>
          </div>
          <div class="nws-entry-foot">
            <label class="check-field"><input type="checkbox" :checked="feed.enabled" @change="setFeed(index, { enabled: target($event).checked })" />启用</label>
            <button type="button" class="secondary-button nws-danger" :aria-label="`删除 RSS 源 ${index + 1}`" @click="removeFeed(index)"><Trash aria-hidden="true" />删除</button>
          </div>
        </fieldset>
      </li>
    </ul>
  </ManagementSection>
</template>
