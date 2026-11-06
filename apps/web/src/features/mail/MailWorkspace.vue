<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { formatTime } from '../time'
import {
  LABELS, SEND_STATUS, downloadAttachment, formatSize, mailError, useMailAccounts, useMailActions, useMailList, useMailSends, useMailThread,
  type MailMessage, type MailSummary,
} from './api'

// 邮件页（T-0130，MAIL_PLAN 第 6 节，owner / admin）：左边标签与搜索，中间邮件列表，右边阅读与回复；
// “发件记录”里有人和智能体经工作台发的信（结果未知的高亮）。邮件内容一律按纯文字显示。
const auth = useAuthStore(), scope = auth.workspaceId!, route = useRoute()
const accounts = useMailAccounts(scope), actions = useMailActions(scope), sends = useMailSends(scope)
const items = computed(() => accounts.data.value?.items ?? [])
const account = ref('')
watch(items, (list) => { if (!list.some(a => a.id === account.value)) account.value = list[0]?.id ?? '' }, { immediate: true })
const current = computed(() => items.value.find(a => a.id === account.value))
const tab = ref<'mail' | 'sends'>(route.query.tab === 'sends' ? 'sends' : 'mail')

const search = ref(''), filters = ref({ q: '', label: 'INBOX', page: null as string | null }), pages = ref<(string | null)[]>([])
const list = useMailList(scope, account, filters)
const thread = ref<string | null>(null)
const opened = useMailThread(scope, account, thread)
const messages = computed(() => opened.data.value?.messages ?? [])
watch([account, () => filters.value.label, () => filters.value.q], () => { thread.value = null; pages.value = [] })

function pick(label: string) { filters.value = { ...filters.value, label, page: null }; pages.value = [] }
function find() { filters.value = { ...filters.value, q: search.value, page: null }; pages.value = [] }
function next() { const token = list.data.value?.next_page_token; if (token) { pages.value.push(filters.value.page); filters.value = { ...filters.value, page: token } } }
function previous() { filters.value = { ...filters.value, page: pages.value.pop() ?? null } }
function open(item: MailSummary) { thread.value = item.thread_id; compose.value = null }

type Draft = { to: string; cc: string; bcc: string; subject: string; body: string; reply: string | null }
const compose = ref<Draft | null>(null), busy = ref(false), error = ref(''), notice = ref('')
function write() { compose.value = { to: '', cc: '', bcc: '', subject: '', body: '', reply: null }; error.value = ''; notice.value = '' }
function reply(message: MailMessage) {
  const address = /<([^>]+)>/.exec(message.sender)?.[1] ?? message.sender
  compose.value = { to: address.trim(), cc: '', bcc: '', subject: '', body: '', reply: message.id }
  error.value = ''; notice.value = ''
}
const split = (value: string) => value.split(/[,;，；\s]+/).map(s => s.trim()).filter(Boolean)
async function send() {
  const draft = compose.value
  if (!draft || busy.value || !account.value) return
  const to = split(draft.to)
  if (!to.length) { error.value = '至少填一个收件人。'; return }
  if (!draft.body.trim()) { error.value = '正文不能为空。'; return }
  if (!draft.reply && !draft.subject.trim()) { error.value = '要写主题。'; return }
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const sent = await actions.send(account.value, {
      to, cc: split(draft.cc), bcc: split(draft.bcc), subject: draft.subject.trim(), body: draft.body,
      ...(draft.reply ? { reply_to_message_id: draft.reply } : {}),
    })
    notice.value = `已发出：${sent.subject}`; compose.value = null
    if (thread.value) await opened.refetch()
  } catch (cause) { error.value = mailError(cause) } finally { busy.value = false }
}
async function download(message: MailMessage, index: number) {
  const attachment = message.attachments[index]
  if (!attachment?.attachment_id) return
  try { await downloadAttachment(scope, account.value, message.id, attachment) } catch (cause) { error.value = mailError(cause) }
}
const sendItems = computed(() => sends.data.value?.items ?? [])
</script>

<template>
  <p v-if="accounts.isPending.value" role="status">正在加载邮箱…</p>
  <p v-else-if="accounts.error.value" role="alert">{{ mailError(accounts.error.value) }}</p>
  <section v-else-if="!items.length" class="panel empty-state">还没有连接 Gmail 邮箱。到 <RouterLink to="/settings/mail">设置 → 邮件</RouterLink> 连接。</section>
  <template v-else>
    <div class="section-heading mail-toolbar">
      <label v-if="items.length > 1" class="form-field"><span>邮箱</span><select v-model="account"><option v-for="a in items" :key="a.id" :value="a.id">{{ a.email }}</option></select></label>
      <p v-else class="form-help">邮箱：{{ current?.email }}</p>
      <div class="form-actions" role="tablist" aria-label="邮件页">
        <button type="button" role="tab" :aria-selected="tab === 'mail'" class="secondary-button" @click="tab = 'mail'">邮件</button>
        <button type="button" role="tab" :aria-selected="tab === 'sends'" class="secondary-button" @click="tab = 'sends'">发件记录</button>
        <button type="button" class="primary-button" :disabled="!current?.can_send || current?.status !== 'active'" @click="tab = 'mail'; write()">写邮件</button>
      </div>
    </div>
    <p v-if="current?.status === 'needs_reconnect'" role="alert" class="feedback">这个邮箱的 Google 授权已失效，请到 <RouterLink to="/settings/mail">设置 → 邮件</RouterLink> 重新连接。</p>
    <p v-if="error" role="alert" class="feedback">{{ error }}</p>
    <p v-if="notice" role="status" class="feedback">{{ notice }}</p>

    <div v-if="tab === 'mail'" class="mail-layout">
      <nav class="panel mail-labels" aria-label="邮件标签">
        <button v-for="l in LABELS" :key="l.id" type="button" class="secondary-button" :aria-pressed="filters.label === l.id" @click="pick(l.id)">{{ l.name }}</button>
        <button type="button" class="secondary-button" :aria-pressed="filters.label === ''" @click="pick('')">全部邮件</button>
      </nav>
      <section class="panel mail-list" aria-label="邮件列表">
        <form class="mail-search" role="search" @submit.prevent="find">
          <label class="form-field"><span>搜索（Gmail 搜索语法）</span><input v-model="search" type="search" maxlength="500" placeholder="from:alice@example.com is:unread newer_than:7d" /></label>
          <button type="submit" class="secondary-button">搜索</button>
        </form>
        <p v-if="list.isPending.value" role="status">正在加载邮件…</p>
        <p v-else-if="list.error.value" role="alert">{{ mailError(list.error.value) }}</p>
        <p v-else-if="!list.data.value?.items.length" class="empty-state">没有邮件。</p>
        <ul class="mail-items">
          <li v-for="item in list.data.value?.items ?? []" :key="item.id">
            <button type="button" class="mail-item" :class="{ unread: item.unread }" :aria-current="thread === item.thread_id" @click="open(item)">
              <span class="mail-item-sender">{{ item.sender }}</span>
              <span class="mail-item-subject">{{ item.subject || '（无主题）' }}</span>
              <span class="mail-item-snippet">{{ item.snippet }}</span>
              <span v-if="item.date" class="mail-item-date">{{ formatTime(item.date) }}</span>
            </button>
          </li>
        </ul>
        <div class="form-actions">
          <button type="button" class="secondary-button" :disabled="!pages.length" @click="previous">上一页</button>
          <button type="button" class="secondary-button" :disabled="!list.data.value?.next_page_token" @click="next">下一页</button>
        </div>
      </section>
      <section class="panel mail-reader" aria-label="阅读">
        <form v-if="compose" class="mail-form" aria-label="写邮件" @submit.prevent="send">
          <h2>{{ compose.reply ? '回复' : '写邮件' }}</h2>
          <label class="form-field"><span>收件人</span><input v-model="compose.to" placeholder="多个地址用逗号分隔" /></label>
          <label class="form-field"><span>抄送</span><input v-model="compose.cc" /></label>
          <label class="form-field"><span>密送</span><input v-model="compose.bcc" /></label>
          <label class="form-field"><span>主题</span><input v-model="compose.subject" maxlength="200" :placeholder="compose.reply ? '不填就用原信的主题加 Re:' : ''" /></label>
          <label class="form-field"><span>正文（纯文字）</span><textarea v-model="compose.body" rows="10" maxlength="20000" /></label>
          <p class="form-help">正文里像是有令牌、密钥时不会发出。收件人合计最多 20 个。</p>
          <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy">发送</button><button type="button" class="secondary-button" @click="compose = null">取消</button></div>
        </form>
        <template v-else-if="thread">
          <p v-if="opened.isPending.value" role="status">正在打开…</p>
          <p v-else-if="opened.error.value" role="alert">{{ mailError(opened.error.value) }}</p>
          <article v-for="message in messages" :key="message.id" class="mail-message" :aria-label="`邮件：${message.subject || '（无主题）'}`">
            <h2>{{ message.subject || '（无主题）' }}</h2>
            <dl class="mail-headers">
              <div><dt>发件人</dt><dd>{{ message.sender }}</dd></div>
              <div><dt>收件人</dt><dd>{{ message.to }}</dd></div>
              <div v-if="message.cc"><dt>抄送</dt><dd>{{ message.cc }}</dd></div>
              <div v-if="message.date"><dt>时间</dt><dd>{{ formatTime(message.date) }}</dd></div>
            </dl>
            <pre class="mail-body">{{ message.text }}</pre>
            <p v-if="message.truncated" class="form-help">正文太长，只显示了前 20000 字，完整内容请在 Gmail 里看。</p>
            <ul v-if="message.attachments.length" class="mail-attachments" aria-label="附件">
              <li v-for="(a, index) in message.attachments" :key="index">
                <span>{{ a.filename }}（{{ formatSize(a.size) }}）</span>
                <button v-if="a.attachment_id" type="button" class="secondary-button" @click="download(message, index)">下载</button>
              </li>
            </ul>
            <div class="form-actions"><button type="button" class="secondary-button" :disabled="!current?.can_send" @click="reply(message)">回复</button></div>
          </article>
        </template>
        <p v-else class="empty-state">选一封邮件阅读。</p>
      </section>
    </div>

    <section v-else class="panel" aria-label="发件记录">
      <p class="form-help">经工作台发的信（人和智能体），不含正文。“结果未知”是 Gmail 没有及时响应：不会自动重发，请到 Gmail 的“已发送”里确认。</p>
      <p v-if="sends.isPending.value" role="status">正在加载…</p>
      <p v-else-if="sends.error.value" role="alert">{{ mailError(sends.error.value) }}</p>
      <p v-else-if="!sendItems.length" class="empty-state">还没有发件记录。</p>
      <table v-else class="agent-table mail-sends">
        <thead><tr><th>时间</th><th>谁发的</th><th>收件人</th><th>主题</th><th>结果</th></tr></thead>
        <tbody>
          <tr v-for="s in sendItems" :key="s.id" :class="{ 'mail-unknown': s.status === 'unknown' }">
            <td>{{ formatTime(s.created_at) }}</td>
            <td>{{ s.actor_kind === 'instance' ? `智能体「${s.actor_name}」` : s.actor_name }}</td>
            <td>{{ [...s.to, ...s.cc].join('、') }}<template v-if="s.bcc_count">（密送 {{ s.bcc_count }}）</template></td>
            <td>{{ s.subject }}</td>
            <td>{{ SEND_STATUS[s.status] }}<template v-if="s.error_code">（{{ s.error_code }}）</template></td>
          </tr>
        </tbody>
      </table>
    </section>
  </template>
</template>
