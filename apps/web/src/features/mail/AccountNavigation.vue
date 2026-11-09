<script setup lang="ts">
import { Inbox, Layers, Mails, Send, SquarePen, Star, TriangleAlert } from '@lucide/vue'
import { CATEGORIES, type MailAccount, type MailAccountState } from './api'
import { ACCOUNT_STATUS, accountName, toneOf } from './format'

// 邮件页左栏（T-0171，效果图 04 / 05）：写邮件、全部邮箱、按邮箱查看（名字、地址、连接状态、有依据的未读数）、邮件分类。
// 分类只放有查询入口的只读分类；没有依据的数字不显示。
const props = defineProps<{
  accounts: MailAccount[]
  states: Record<string, MailAccountState>
  unread: Record<string, number>
  accountId: string | null
  label: string
  canWrite: boolean
}>()
const emit = defineEmits<{ write: []; account: [id: string | null]; label: [id: string] }>()
const ICONS: Record<string, typeof Mails> = { '': Mails, INBOX: Inbox, STARRED: Star, SENT: Send }

// 连接状态：设置里的状态优先（需要重新连接），其次是最近一批读取的状态（限流 / 暂时读不到）
function problem(account: MailAccount): string | null {
  if (account.status !== 'active') return ACCOUNT_STATUS.needs_reconnect
  if (!account.can_read) return '没有读取权限'
  const state = props.states[account.id]
  if (state && ['needs_reconnect', 'rate_limited', 'unavailable'].includes(state.status)) return ACCOUNT_STATUS[state.status]
  return null
}
</script>

<template>
  <nav class="mx-nav" aria-label="邮箱与分类">
    <button type="button" class="primary-button mx-write" :disabled="!canWrite" :title="canWrite ? undefined : '没有可以发信的邮箱'" @click="emit('write')">
      <SquarePen aria-hidden="true" />写邮件
    </button>
    <button type="button" class="mx-nav-item mx-all" :aria-pressed="accountId === null" @click="emit('account', null)">
      <Layers aria-hidden="true" /><span class="mx-nav-text">全部邮箱</span><span class="mx-count"><span class="sr-only">，共 </span>{{ accounts.length }}<span class="sr-only"> 个邮箱</span></span>
    </button>
    <h2 class="mx-nav-heading">按邮箱查看</h2>
    <ul class="mx-accounts" aria-label="按邮箱查看">
      <li v-for="(account, index) in accounts" :key="account.id">
        <button type="button" class="mx-nav-item mx-account" :aria-pressed="accountId === account.id" :title="account.email" @click="emit('account', account.id)">
          <span class="mx-dot" :style="{ '--tone': toneOf(index) }" aria-hidden="true" />
          <span class="mx-account-text">
            <span class="mx-account-name">{{ accountName(account) }}</span>
            <span v-if="accountName(account) !== account.email" class="mx-account-email">{{ account.email }}</span>
            <span v-if="problem(account)" class="mx-account-problem"><TriangleAlert aria-hidden="true" />{{ problem(account) }}</span>
          </span>
          <span v-if="unread[account.id] !== undefined" class="mx-count" title="收件箱未读"><span class="sr-only">，收件箱未读 </span>{{ unread[account.id] }}</span>
        </button>
      </li>
    </ul>
    <h2 class="mx-nav-heading">邮件分类</h2>
    <ul class="mx-categories" aria-label="邮件分类">
      <li v-for="category in CATEGORIES" :key="category.id">
        <button type="button" class="mx-nav-item" :aria-pressed="label === category.id" @click="emit('label', category.id)">
          <component :is="ICONS[category.id] ?? Mails" aria-hidden="true" /><span class="mx-nav-text">{{ category.name }}</span>
        </button>
      </li>
    </ul>
  </nav>
</template>
