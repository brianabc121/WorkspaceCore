<script setup lang="ts">
import { computed, ref, toRef, watch } from 'vue'
import { ChevronDown, FileText, Info, Reply, TriangleAlert } from '@lucide/vue'
import { formatTime } from '../time'
import { downloadAttachment, formatSize, mailError, useMailMessageOf, useMailThreadOf, type MailMessage, type SelectedMail } from './api'
import { LABEL_NAMES, parseSender } from './format'
import type { Owner } from './types'

// 邮件页右栏（T-0171，效果图 04 / 05）：所属邮箱、主题、发件人、纯文字正文（不渲染 HTML、不加载远程图片）、
// 附件（经工作台下载，25 MB 以内）、往来邮件、“将使用 xx 回复”。读信、会话、附件都按所属邮箱 + ID 请求。
// 只有“回复”：Gmail 只授权了 readonly + send，归档、删除、标记已读 / 星标这些写操作不提供；回复全部、转发本轮不做。
const props = defineProps<{ scope: string; selected: SelectedMail; owner: Owner }>()
const emit = defineEmits<{ reply: [message: MailMessage] }>()

const selected = toRef(props, 'selected')
const thread = useMailThreadOf(props.scope, selected)
const inThread = computed(() => thread.data.value?.messages.find((m) => m.id === props.selected.messageId) ?? null)
// 会话太长（只给最近 20 封）时选中的那封可能不在里面，单独读
const fallback = computed(() => (thread.data.value && !inThread.value ? props.selected : null))
const single = useMailMessageOf(props.scope, fallback)
const message = computed(() => inThread.value ?? single.data.value ?? null)
const others = computed(() => (thread.data.value?.messages ?? []).filter((m) => m.id !== props.selected.messageId))
const pending = computed(() => thread.isPending.value || (!!fallback.value && single.isPending.value))
const error = computed(() => thread.error.value ?? single.error.value)
const sender = computed(() => parseSender(message.value?.sender ?? ''))
const labels = computed(() => (message.value?.labels ?? []).filter((l) => LABEL_NAMES[l] && l !== 'UNREAD').map((l) => LABEL_NAMES[l]!))
const downloadError = ref('')
watch(() => `${props.selected.accountId}:${props.selected.messageId}`, () => { downloadError.value = '' })

async function download(item: MailMessage, index: number) {
  const attachment = item.attachments[index]
  if (!attachment?.attachment_id) return
  downloadError.value = ''
  // 附件属于这封信所在的邮箱：用选中项的 accountId，不用列表当前的范围
  try { await downloadAttachment(props.scope, props.selected.accountId, item.id, attachment) } catch (cause) { downloadError.value = mailError(cause) }
}
</script>

<template>
  <div class="mx-reader">
    <p class="mx-owner" :style="{ '--tone': owner.tone }"><span class="mx-dot" aria-hidden="true" />所属邮箱：{{ owner.name }}<template v-if="owner.name !== owner.email"> · {{ owner.email }}</template></p>
    <p v-if="pending" class="ws-state-line" role="status">正在打开…</p>
    <div v-else-if="error" class="ws-callout" role="alert"><strong><TriangleAlert aria-hidden="true" />打不开这封邮件</strong><p>{{ mailError(error) }}</p></div>
    <article v-else-if="message" class="mx-message" :aria-label="`邮件：${message.subject || '（无主题）'}`">
      <h2 class="mx-subject">{{ message.subject || '（无主题）' }}</h2>
      <p v-if="labels.length" class="mx-labels"><span v-for="name in labels" :key="name" class="mx-label">{{ name }}</span></p>
      <div class="mx-from">
        <span class="mx-avatar" aria-hidden="true">{{ sender.name.slice(0, 1).toUpperCase() }}</span>
        <div class="mx-from-text">
          <p><strong>{{ sender.name }}</strong><span v-if="sender.address !== sender.name" class="mx-address">&lt;{{ sender.address }}&gt;</span></p>
          <p class="mx-muted">收件人：{{ message.to || '—' }}</p>
          <p v-if="message.cc" class="mx-muted">抄送：{{ message.cc }}</p>
        </div>
        <time v-if="message.date" class="mx-muted mx-time" :datetime="message.date">{{ formatTime(message.date) }}</time>
      </div>
      <pre class="mx-body">{{ message.text || '（没有正文）' }}</pre>
      <p v-if="message.truncated" class="form-help">正文太长，只显示了前 20000 字，完整内容请在 Gmail 里看。</p>
      <ul v-if="message.attachments.length" class="mx-files" aria-label="附件">
        <li v-for="(a, index) in message.attachments" :key="index">
          <button type="button" class="mx-file" :disabled="!a.attachment_id" :title="a.attachment_id ? '经工作台下载（不在浏览器里打开）' : '这个附件不能下载'" @click="download(message, index)">
            <FileText aria-hidden="true" /><span class="mx-file-name">{{ a.filename || '附件' }}</span><span class="mx-muted">· {{ formatSize(a.size) }}</span><span class="sr-only">，下载</span>
          </button>
        </li>
      </ul>
      <p v-if="downloadError" role="alert" class="feedback">{{ downloadError }}</p>
      <details class="mx-thread">
        <summary><span>往来邮件 {{ (thread.data.value?.messages.length ?? 1) }} 封</span><ChevronDown aria-hidden="true" /></summary>
        <p v-if="!others.length" class="mx-muted">这个会话里只有这一封。</p>
        <ol v-else class="mx-thread-list">
          <li v-for="m in others" :key="m.id">
            <p class="mx-thread-head"><strong>{{ parseSender(m.sender).name }}</strong><time v-if="m.date" class="mx-muted" :datetime="m.date">{{ formatTime(m.date) }}</time></p>
            <p class="mx-thread-subject">{{ m.subject || '（无主题）' }}</p>
            <pre class="mx-body is-quoted">{{ m.text || '（没有正文）' }}</pre>
          </li>
        </ol>
      </details>
      <p class="mx-reply-as" :class="{ 'is-blocked': !owner.canSend }">
        <Info aria-hidden="true" />
        <template v-if="owner.canSend">将使用 {{ owner.name === owner.email ? owner.email : `${owner.name} ${owner.email}` }} 回复。</template>
        <template v-else>{{ owner.reason ?? '这个邮箱不能发信' }}，不能回复。</template>
      </p>
      <div class="mx-reader-actions">
        <button type="button" class="primary-button" :disabled="!owner.canSend" @click="emit('reply', message)"><Reply aria-hidden="true" />回复</button>
      </div>
    </article>
  </div>
</template>
