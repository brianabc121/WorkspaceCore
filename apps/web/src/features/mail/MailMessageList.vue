<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowLeft, Search, Star, TriangleAlert } from '@lucide/vue'
import { ApiError } from '../../services/api'
import { CATEGORIES, FILTERS, mailError, mailKeyOf, type MailAccount, type MailFilter, type MailItem, type MailPage } from './api'
import { accountName, accountProblem, accountTag, parseSender, shortTime, toneOf } from './format'

// 邮件页中栏（T-0171，效果图 04 / 05）：搜索（提示作用范围）、全部 / 未读 / 有附件、邮件列表（每行标明所属邮箱）、
// “已加载 N 封 · 加载更多”。总数只在服务端给了估算时显示“约 N 封”，部分邮箱失败时不显示总数。
const props = defineProps<{
  items: MailItem[]
  accounts: MailAccount[]
  currentId: string | null
  label: string
  filter: MailFilter
  query: string
  selectedKey: string | null
  latest: MailPage | null
  pending: boolean
  error: unknown
  hasMore: boolean
  loadingMore: boolean
  moreError: unknown
}>()
const emit = defineEmits<{ search: [text: string]; filter: [id: MailFilter]; select: [item: MailItem]; more: []; back: []; reload: [] }>()

const text = ref(props.query)
watch(() => props.query, (value) => { text.value = value })
const current = computed(() => (props.currentId ? props.accounts.find((a) => a.id === props.currentId) ?? null : null))
const category = computed(() => CATEGORIES.find((c) => c.id === props.label)?.name ?? '全部邮件')
const scopeName = computed(() => (current.value ? accountName(current.value) : null))
const placeholder = computed(() => (props.currentId ? `搜索${scopeName.value ? ` ${scopeName.value} ` : '这个邮箱'}的邮件` : '搜索所有邮箱的邮件'))
const tones = computed(() => new Map(props.accounts.map((a, index) => [a.id, toneOf(index)])))
const tagOf = (item: MailItem) => accountTag({ display_name: item.account_display_name, email: item.account_email })

// 读不到的邮箱逐个说明；整体失败、全部需要重连、没读完分别给出标题
const problems = computed(() => (props.latest?.accounts ?? []).filter((a) => ['needs_reconnect', 'rate_limited', 'unavailable', 'pending'].includes(a.status)))
const notice = computed(() => {
  const page = props.latest
  if (!page || !problems.value.length) return null
  const single = !!props.currentId
  if (page.outcome === 'no_access') return single ? '这个邮箱需要重新连接' : '邮箱都需要重新连接'
  if (page.outcome === 'all_failed') return single ? '这个邮箱暂时读不到' : '邮箱都暂时读不到'
  if (page.outcome === 'pending') return '邮箱响应较慢，还在读取'
  if (problems.value.every((a) => a.status === 'pending')) return '部分邮箱还没读完'
  return single ? '这个邮箱暂时读不到' : '部分邮箱暂不可用'
})
const missing = computed(() => props.error instanceof ApiError && props.error.status === 404 && !!props.currentId)
const expired = computed(() => props.moreError instanceof ApiError && ['MAIL_CURSOR_EXPIRED', 'MAIL_CURSOR_MISMATCH'].includes(props.moreError.code))
const empty = computed(() => {
  if (props.pending || props.error || props.items.length || !props.latest) return null
  if (!['ok', 'partial'].includes(props.latest.outcome)) return null
  return props.query || props.filter !== 'all' ? '没有找到符合条件的邮件。' : props.currentId ? '这个邮箱里没有邮件。' : '没有邮件。'
})
function submit() { emit('search', text.value) }
</script>

<template>
  <section class="mx-list-col" aria-label="邮件列表">
    <form class="mx-search" role="search" @submit.prevent="submit">
      <Search aria-hidden="true" />
      <input v-model="text" type="search" maxlength="400" :placeholder="placeholder" :aria-label="placeholder" title="支持 Gmail 搜索语法，例如 from:alice@example.com newer_than:7d" />
      <button type="submit" class="sr-only">搜索</button>
    </form>
    <div class="mx-list-head">
      <h2 v-if="!currentId">{{ category }}<span class="mx-list-sub">来自 {{ accounts.length }} 个邮箱</span></h2>
      <h2 v-else>{{ scopeName ?? '这个邮箱' }}<span v-if="current && scopeName !== current.email" class="mx-list-sub">{{ current.email }}</span><span v-if="label" class="mx-list-sub">· {{ category }}</span></h2>
      <button v-if="currentId" type="button" class="mx-link" @click="emit('back')"><ArrowLeft aria-hidden="true" />返回全部邮箱</button>
    </div>
    <div class="ws-chips mx-filters" role="group" aria-label="筛选">
      <button v-for="f in FILTERS" :key="f.id" type="button" class="ws-chip" :aria-pressed="filter === f.id" @click="emit('filter', f.id)">{{ f.name }}</button>
    </div>

    <div v-if="notice" class="ws-callout tone-amber mx-notice" role="alert">
      <strong><TriangleAlert aria-hidden="true" />{{ notice }}</strong>
      <ul>
        <li v-for="a in problems" :key="a.account_id">
          {{ accountName(a) }}：{{ accountProblem(a) }}<template v-if="a.status === 'needs_reconnect'">（<RouterLink to="/settings/mail">去重新连接</RouterLink>）</template>
        </li>
      </ul>
    </div>

    <p v-if="pending" class="ws-state-line" role="status">正在读取邮件…</p>
    <div v-else-if="error" class="ws-callout mx-notice" role="alert">
      <strong><TriangleAlert aria-hidden="true" />{{ missing ? '这个邮箱已断开或不存在' : '读取邮件失败' }}</strong>
      <p>{{ missing ? '不会自动换成别的邮箱。可以返回全部邮箱，或到 设置 → 邮件 查看。' : mailError(error) }}</p>
    </div>
    <p v-else-if="empty" class="ws-state-line">{{ empty }}</p>

    <ul class="mx-rows">
      <li v-for="item in items" :key="mailKeyOf(item)">
        <button type="button" class="mx-row" :class="{ 'is-unread': item.unread }" :aria-current="selectedKey === mailKeyOf(item) ? 'true' : undefined" @click="emit('select', item)">
          <span class="mx-row-top">
            <span class="mx-unread-dot" aria-hidden="true" />
            <span class="mx-row-sender">{{ parseSender(item.sender).name }}</span>
            <span v-if="item.unread" class="sr-only">（未读）</span>
            <Star v-if="item.labels.includes('STARRED')" class="mx-star" aria-hidden="true" /><span v-if="item.labels.includes('STARRED')" class="sr-only">（已加星标）</span>
            <span class="mx-tag" :style="{ '--tone': tones.get(item.account_id) ?? toneOf(0) }" :title="item.account_email"><span class="sr-only">所属邮箱：</span>{{ tagOf(item) }}</span>
            <time class="mx-row-time" :datetime="item.date ?? undefined">{{ shortTime(item.date) }}</time>
          </span>
          <span class="mx-row-subject">{{ item.subject || '（无主题）' }}</span>
          <span class="mx-row-snippet">{{ item.snippet }}</span>
        </button>
      </li>
    </ul>

    <div v-if="items.length || hasMore" class="mx-list-foot">
      <span class="mx-loaded">已加载 {{ items.length }} 封<template v-if="latest && latest.total_estimate !== null"> · 约 {{ latest.total_estimate }} 封</template></span>
      <button v-if="hasMore && !expired" type="button" class="secondary-button" :disabled="loadingMore" @click="emit('more')">{{ loadingMore ? '正在加载…' : '加载更多' }}</button>
    </div>
    <p v-if="moreError" class="mx-more-error" role="alert">
      <template v-if="expired">列表已过期（超过 15 分钟或邮箱有变化）。<button type="button" class="mx-link" @click="emit('reload')">从第一页重新加载</button></template>
      <template v-else>{{ mailError(moreError) }}</template>
    </p>
  </section>
</template>
