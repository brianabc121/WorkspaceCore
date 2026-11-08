<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { safeUrl } from '../../widgets/registry'
import { newsError, useNewsActions, type NewsItem } from './api'

// 一条新闻：标题是外部链接（后端只留 http(s)，这里再查一次），来源、排名、时间、命中的主题；
// 收藏 / 不感兴趣只给成员以上（访客不显示，后端也会拒绝）。owner 标“不感兴趣”时可以顺手把一个词加进全局排除。
const props = defineProps<{ item: NewsItem; hideTopic?: boolean }>()
const auth = useAuthStore(), actions = useNewsActions(auth.workspaceId!)
const canFeedback = computed(() => ['owner', 'admin', 'member'].includes(auth.currentWorkspace?.role ?? ''))
const busy = ref(false), error = ref(''), asking = ref(false), word = ref('')
const linkable = computed(() => safeUrl(props.item.url))
const label = computed(() => props.item.title.trim() || props.item.url)

async function run(effect: () => Promise<unknown>) {
  if (busy.value) return false
  busy.value = true; error.value = ''
  try { await effect(); return true }
  catch (cause) { error.value = newsError(cause); return false }
  finally { busy.value = false }
}
function favorite() { void run(() => actions.favorite(props.item)) }
function dismiss() {
  if (auth.isOwner) { asking.value = !asking.value; return }
  void run(() => actions.dismiss(props.item))
}
async function confirmDismiss() {
  if (await run(() => actions.dismiss(props.item, word.value))) { asking.value = false; word.value = '' }
}
</script>

<template>
  <li class="news-item" :aria-label="label">
    <div class="news-item-main">
      <a v-if="linkable" class="news-item-title" :href="item.url" target="_blank" rel="noopener noreferrer">{{ label }}</a>
      <span v-else class="news-item-title">{{ label }}</span>
      <p class="news-meta">
        <span v-if="item.source_name">{{ item.source_name }}</span>
        <span v-if="item.rank !== null">第 {{ item.rank }} 名</span>
        <span v-if="item.time">{{ item.time }}</span>
        <span v-if="item.topic && !hideTopic" class="chip">{{ item.topic }}</span>
        <span v-if="item.dismissed" class="chip">已标为不感兴趣</span>
      </p>
      <p v-if="item.summary" class="news-item-summary">{{ item.summary }}</p>
    </div>
    <div v-if="canFeedback" class="news-item-actions">
      <button type="button" class="secondary-button" :aria-pressed="!!item.favorite_id" :disabled="busy" @click="favorite">{{ item.favorite_id ? '已收藏' : '收藏' }}</button>
      <button v-if="!item.dismissed" type="button" class="secondary-button" :aria-expanded="auth.isOwner ? asking : undefined" :disabled="busy" @click="dismiss">不感兴趣</button>
    </div>
    <form v-if="asking" class="news-dismiss" aria-label="标为不感兴趣" @submit.prevent="confirmDismiss">
      <label class="form-field"><span>同时把一个词加入全局排除（可不填）</span><input v-model="word" maxlength="60" placeholder="例如标题里的某个词" :disabled="busy" /></label>
      <button type="submit" class="primary-button" :disabled="busy">标为不感兴趣</button>
      <button type="button" class="secondary-button" :disabled="busy" @click="asking = false">取消</button>
    </form>
    <p v-if="error" role="alert" class="feedback">{{ error }}</p>
  </li>
</template>
