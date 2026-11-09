<script setup lang="ts">
import { computed, reactive } from 'vue'
import { Info, Send } from '@lucide/vue'
import type { MailAccount } from './api'
import { accountName } from './format'
import type { Draft } from './types'

// 写邮件 / 回复（T-0171，规范 7.2）。发信邮箱必须明确：
// - 全部邮箱视图写新邮件时没有默认值，必须自己选一个能发信的邮箱（不悄悄用第一个）；
// - 回复固定用原邮件所属的邮箱；换发信邮箱要确认，并且清除回复关联（不会把 A 邮箱的 reply_to 发到 B 邮箱）。
// 发信仍走原来的接口：收件人合计最多 20 个、密钥拦截、CSRF；结果未知不自动重发（见 MailWorkspace）。
const props = defineProps<{ initial: Draft; sendable: MailAccount[]; busy: boolean; error: string; unknown: boolean }>()
const emit = defineEmits<{ send: [draft: Draft]; cancel: [] }>()

const draft = reactive<Draft>({ ...props.initial, reply: props.initial.reply ? { ...props.initial.reply } : null })
const sender = computed(() => props.sendable.find((a) => a.id === draft.senderId) ?? null)

function changeSender(event: Event) {
  const select = event.target as HTMLSelectElement
  const next = select.value
  if (draft.reply && next !== draft.reply.accountId) {
    const target = props.sendable.find((a) => a.id === next)
    const ok = window.confirm(`换成 ${target ? accountName(target) : '另一个邮箱'} 发信会改变发件人，并取消与原邮件的回复关联（对方收到的是一封新邮件）。确定要换吗？`)
    if (!ok) { select.value = draft.senderId; return }
    const subject = draft.reply.subject.trim()
    draft.reply = null
    if (!draft.subject.trim()) draft.subject = subject.toLowerCase().startsWith('re:') ? subject : subject ? `Re: ${subject}` : ''
  }
  draft.senderId = next
}
function dirty() { return !!(draft.body.trim() || draft.to.trim() || draft.cc.trim() || draft.bcc.trim() || (!draft.reply && draft.subject.trim())) }
defineExpose({ dirty, isReply: () => !!draft.reply })
</script>

<template>
  <form class="mx-composer mail-form" aria-label="写邮件" @submit.prevent="emit('send', { ...draft, reply: draft.reply ? { ...draft.reply } : null })">
    <h2>{{ draft.reply ? '回复' : '写邮件' }}</h2>
    <p v-if="draft.reply" class="mx-reply-as"><Info aria-hidden="true" />回复“{{ draft.reply.subject || '（无主题）' }}”：固定用原邮件所属的邮箱发出；换发信邮箱会取消回复关联。</p>
    <label class="form-field"><span>发信邮箱</span>
      <select :value="draft.senderId" @change="changeSender">
        <option value="" disabled>请选择发信邮箱</option>
        <option v-for="a in sendable" :key="a.id" :value="a.id">{{ accountName(a) }}{{ accountName(a) !== a.email ? `（${a.email}）` : '' }}</option>
      </select>
    </label>
    <p v-if="sender" class="form-help">将以 {{ accountName(sender) }}<template v-if="accountName(sender) !== sender.email"> &lt;{{ sender.email }}&gt;</template> 的身份发出。</p>
    <p v-else class="form-help">还没有选择发信邮箱。</p>
    <label class="form-field"><span>收件人</span><input v-model="draft.to" placeholder="多个地址用逗号分隔" /></label>
    <label class="form-field"><span>抄送</span><input v-model="draft.cc" /></label>
    <label class="form-field"><span>密送</span><input v-model="draft.bcc" /></label>
    <label class="form-field"><span>主题</span><input v-model="draft.subject" maxlength="200" :placeholder="draft.reply ? '不填就用原信的主题加 Re:' : ''" /></label>
    <label class="form-field"><span>正文（纯文字）</span><textarea v-model="draft.body" rows="10" maxlength="20000" /></label>
    <p class="form-help">正文里像是有令牌、密钥时不会发出。收件人合计最多 20 个。</p>
    <p v-if="unknown" class="ws-callout tone-amber mx-unknown" role="status">上一次发送结果未知，不会自动重发。请先到 Gmail 的“已发送”里确认，确实没发出再点发送。</p>
    <p v-if="error" role="alert" class="feedback">{{ error }}</p>
    <div class="form-actions">
      <button type="submit" class="primary-button" :disabled="busy"><Send aria-hidden="true" />{{ busy ? '正在发送…' : '发送' }}</button>
      <button type="button" class="secondary-button" :disabled="busy" @click="emit('cancel')">取消</button>
    </div>
  </form>
</template>
