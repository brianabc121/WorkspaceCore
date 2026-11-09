<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, MailOpen, RefreshCw, Settings } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError } from '../../services/api'
import { useMediaQuery } from '../../composables/useMediaQuery'
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet'
import { mailError, useMailAccounts, useMailActions, type MailMessage } from './api'
import { accountName, parseSender, toneOf } from './format'
import { useMailScope } from './useMailScope'
import type { Draft, Owner } from './types'
import AccountNavigation from './AccountNavigation.vue'
import MailMessageList from './MailMessageList.vue'
import MailReader from './MailReader.vue'
import MailComposer from './MailComposer.vue'
import MailSends from './MailSends.vue'
import '../workspace/workspace-pages.css'
import './mail-pages.css'

// 邮件页（T-0171，四页改造规范第 7 节，效果图 04 / 05；owner / admin）：
// 默认全部邮箱 + 全部邮件（不默认选第一个邮箱），左栏按邮箱 / 分类切换，中栏列表，右栏阅读与回复。
// ≥1440px 三栏；更窄时阅读器改成右侧抽屉（手机上铺满，列表 → 详情逐级返回）。
// 邮件内容只在内存：离开邮件页、换工作空间（整页重建）、退出或失权时清掉。
const auth = useAuthStore(), scope = auth.workspaceId!, route = useRoute(), router = useRouter()
const accounts = useMailAccounts(scope), actions = useMailActions(scope)
const docked = useMediaQuery('(min-width: 1440px)')
const tab = ref<'mail' | 'sends'>(route.query.tab === 'sends' ? 'sends' : 'mail')
const accountList = computed(() => accounts.data.value?.items ?? [])
const sendable = computed(() => accountList.value.filter((a) => a.status === 'active' && a.can_send))
const activeCount = computed(() => accountList.value.filter((a) => a.status === 'active').length)

type Compose = { key: number; draft: Draft }
const compose = ref<Compose | null>(null)
const composer = ref<InstanceType<typeof MailComposer> | null>(null)
let composeSeq = 0
const busy = ref(false), sendError = ref(''), unknown = ref(false), notice = ref('')

// 正在写的回复与选中的邮件绑在一起：换范围前确认是否放弃；新邮件有自己的发信邮箱，换范围不受影响
function canLeave() {
  const form = composer.value
  if (!compose.value || !form?.isReply()) return true
  if (form.dirty() && !window.confirm('切换后会放弃这封还没发出的回复，确定吗？')) return false
  compose.value = null
  return true
}
const mail = useMailScope(scope, { initialAccount: typeof route.query.account === 'string' ? route.query.account : null, canLeave })
// 单个邮箱可以用 ?account= 深链；换回全部邮箱时去掉
watch(mail.accountId, (id) => { void router.replace({ query: { ...route.query, account: id ?? undefined } }) })
watch(mail.accountId, () => { notice.value = '' })

const list = mail.list
const pending = computed(() => list.isPending.value)
const listError = computed(() => (list.data.value ? null : list.error.value))
const moreError = computed(() => (list.isFetchNextPageError.value ? list.error.value : null))
const syncedAt = computed(() => (list.dataUpdatedAt.value ? new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(list.dataUpdatedAt.value) : null))
const selectedKey = computed(() => (mail.selected.value ? `${mail.selected.value.accountId}:${mail.selected.value.messageId}` : null))

// 失权（403）：清掉已经读到的邮件内容，不再显示
watch(() => list.error.value, (error) => { if (error instanceof ApiError && error.status === 403) mail.forget() })
onBeforeUnmount(() => mail.forget())

// 选中邮件所属的邮箱：阅读器顶部显示，回复固定用它
function ownerOf(id: string): Owner {
  const index = accountList.value.findIndex((a) => a.id === id)
  const account = accountList.value[index]
  const item = mail.items.value.find((i) => i.account_id === id)
  const name = account ? accountName(account) : item ? accountName({ display_name: item.account_display_name, email: item.account_email }) : '已断开的邮箱'
  const reason = !account ? '这个邮箱已断开' : account.status !== 'active' ? '这个邮箱需要重新连接' : !account.can_send ? '这个邮箱没有授权发信' : null
  return { id, name, email: account?.email ?? item?.account_email ?? '', tone: toneOf(Math.max(index, 0)), canSend: reason === null, reason }
}
const owner = computed(() => (mail.selected.value ? ownerOf(mail.selected.value.accountId) : null))

function open(draft: Draft) {
  if (compose.value && composer.value?.dirty() && !window.confirm('放弃正在写的邮件？')) return
  compose.value = { key: ++composeSeq, draft }
  sendError.value = ''; unknown.value = false; notice.value = ''
}
// 写新邮件：全部邮箱视图下不预选发信邮箱；单个邮箱视图下默认用这个邮箱（能发信时）
function write() {
  const current = mail.accountId.value
  const senderId = current && sendable.value.some((a) => a.id === current) ? current : ''
  open({ senderId, to: '', cc: '', bcc: '', subject: '', body: '', reply: null })
}
function reply(message: MailMessage) {
  const selected = mail.selected.value
  if (!selected) return
  open({ senderId: selected.accountId, to: parseSender(message.sender).address, cc: '', bcc: '', subject: '', body: '', reply: { accountId: selected.accountId, messageId: message.id, subject: message.subject } })
}
function closeReader() {
  if (compose.value) {
    if (composer.value?.dirty() && !window.confirm('放弃正在写的邮件？')) return
    compose.value = null
    return
  }
  mail.selected.value = null
}

const split = (value: string) => value.split(/[,;，；\s]+/).map((s) => s.trim()).filter(Boolean)
async function send(draft: Draft) {
  if (busy.value) return
  sendError.value = ''
  const account = sendable.value.find((a) => a.id === draft.senderId)
  if (!account) { sendError.value = draft.senderId ? '这个邮箱现在不能发信（需要重新连接或没有发信授权）。' : '请先选择发信邮箱。'; return }
  // 回复关联只属于原邮件所在的邮箱：绝不把 A 邮箱的 reply_to 发到 B 邮箱
  if (draft.reply && draft.reply.accountId !== account.id) { sendError.value = '回复只能用原邮件所属的邮箱发出。'; return }
  const to = split(draft.to)
  if (!to.length) { sendError.value = '至少填一个收件人。'; return }
  if (!draft.body.trim()) { sendError.value = '正文不能为空。'; return }
  if (!draft.reply && !draft.subject.trim()) { sendError.value = '要写主题。'; return }
  if (unknown.value && !window.confirm('上一次发送结果未知，可能已经发出。确认在 Gmail 的“已发送”里没有这封信，再发一次吗？')) return
  busy.value = true
  try {
    const sent = await actions.send(account.id, {
      to, cc: split(draft.cc), bcc: split(draft.bcc), subject: draft.subject.trim(), body: draft.body,
      ...(draft.reply ? { reply_to_message_id: draft.reply.messageId } : {}),
    })
    notice.value = `已发出：${sent.subject}`
    compose.value = null; unknown.value = false
    if (draft.reply) await mail.refreshOpened()
  } catch (cause) {
    sendError.value = mailError(cause)
    if (cause instanceof ApiError && ['TIMEOUT_UNKNOWN', 'WRITE_UNKNOWN'].includes(cause.code)) unknown.value = true
  } finally { busy.value = false }
}
async function refresh() { notice.value = ''; await mail.refresh() }
async function more() { if (list.hasNextPage.value && !list.isFetchingNextPage.value) await list.fetchNextPage() }
</script>

<template>
  <header class="ws-header mx-header">
    <div>
      <h1>邮件</h1>
      <p>统一查看全部邮箱，也可以按邮箱单独查看</p>
    </div>
    <div class="ws-actions">
      <span v-if="accountList.length" class="ws-meta mx-sync"><span class="mx-live" aria-hidden="true" />{{ activeCount }} 个邮箱已连接<template v-if="syncedAt"> · 最近读取 {{ syncedAt }}</template></span>
      <div class="ws-segment mx-tabs" role="tablist" aria-label="邮件页">
        <button type="button" role="tab" :aria-selected="tab === 'mail'" @click="tab = 'mail'">邮件</button>
        <button type="button" role="tab" :aria-selected="tab === 'sends'" @click="tab = 'sends'">发件记录</button>
      </div>
      <button v-if="tab === 'mail' && accountList.length" type="button" class="secondary-button" :disabled="list.isFetching.value" @click="refresh">
        <RefreshCw aria-hidden="true" />{{ mail.accountId.value ? '刷新当前邮箱' : '刷新全部' }}
      </button>
      <RouterLink to="/settings/mail" class="secondary-button mx-manage"><Settings aria-hidden="true" />管理邮箱</RouterLink>
    </div>
  </header>

  <p v-if="accounts.isPending.value" role="status">正在加载邮箱…</p>
  <p v-else-if="accounts.error.value" role="alert">{{ mailError(accounts.error.value) }}</p>
  <section v-else-if="!accountList.length" class="ws-panel ws-panel-body empty-state">还没有连接 Gmail 邮箱。到 <RouterLink to="/settings/mail">设置 → 邮件</RouterLink> 连接。</section>
  <template v-else>
    <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
    <MailSends v-if="tab === 'sends'" :scope="scope" />
    <div v-else class="ws-panel mx-shell" :class="{ 'is-docked': docked }">
      <AccountNavigation
        :accounts="accountList" :states="mail.states.value" :unread="mail.unread.value" :account-id="mail.accountId.value" :label="mail.label.value" :can-write="sendable.length > 0"
        @write="write" @account="mail.setAccount" @label="mail.setLabel"
      />
      <MailMessageList
        :items="mail.items.value" :accounts="accountList" :current-id="mail.accountId.value" :label="mail.label.value" :filter="mail.filter.value" :query="mail.query.value"
        :selected-key="selectedKey" :latest="mail.latest.value" :pending="pending" :error="listError" :has-more="list.hasNextPage.value"
        :loading-more="list.isFetchingNextPage.value" :more-error="moreError"
        @search="mail.search" @filter="mail.setFilter" @select="mail.select" @more="more" @back="mail.setAccount(null)" @reload="refresh"
      />
      <section v-if="docked" class="mx-reader-col" aria-label="阅读">
        <MailComposer v-if="compose" ref="composer" :key="compose.key" :initial="compose.draft" :sendable="sendable" :busy="busy" :error="sendError" :unknown="unknown" @send="send" @cancel="closeReader" />
        <MailReader v-else-if="mail.selected.value && owner" :scope="scope" :selected="mail.selected.value" :owner="owner" @reply="reply" />
        <div v-else class="mx-reader-empty"><MailOpen aria-hidden="true" /><p>选一封邮件阅读。</p></div>
      </section>
    </div>
    <Sheet v-if="tab === 'mail' && !docked" :open="!!compose || !!mail.selected.value" @update:open="(value: boolean) => { if (!value) closeReader() }">
      <SheetContent side="right" class="mx-sheet w-full gap-0 p-0 sm:max-w-[680px]" :aria-describedby="undefined">
        <SheetTitle class="sr-only">{{ compose ? '写邮件' : '阅读邮件' }}</SheetTitle>
        <div class="mx-sheet-head"><button type="button" class="mx-link" @click="closeReader"><ArrowLeft aria-hidden="true" />返回列表</button></div>
        <div class="mx-sheet-body">
          <MailComposer v-if="compose" ref="composer" :key="compose.key" :initial="compose.draft" :sendable="sendable" :busy="busy" :error="sendError" :unknown="unknown" @send="send" @cancel="closeReader" />
          <MailReader v-else-if="mail.selected.value && owner" :scope="scope" :selected="mail.selected.value" :owner="owner" @reply="reply" />
        </div>
      </SheetContent>
    </Sheet>
  </template>
</template>
