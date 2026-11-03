<script setup lang="ts">
import { computed, toRef } from 'vue'
import { Check, CircleAlert, CircleHelp, Clock, EllipsisVertical, Pin, PinOff, RotateCw } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { useMediaUrl } from './media'
import { clockTime, colorIndex } from './format'
import { usePopover } from './usePopover'
import ApprovalCard from '../ai/ApprovalCard.vue'
import ChatAvatar from './ChatAvatar.vue'

// 一条消息（T-0095）：自己的在右、别人的在左；群里显示发言人的名字与头像；时间与发送状态在气泡右下角。
const props = defineProps<{ scope: string; message: Schema['ImMessageOut']; sender: string; mine: boolean; selfId?: string | null; group: boolean; first: boolean; last: boolean; canPin: boolean; canResend: boolean; locked: boolean }>()
const emit = defineEmits<{ pin: []; unpin: []; resend: [] }>()
const media = toRef(() => props.message.kind === 'image' ? props.message.image?.media_id ?? null : null)
const { url, failed } = useMediaUrl(props.scope, media)
const mentionsMe = computed(() => !!props.selfId && props.message.at_im_ids.includes(props.selfId))
const showName = computed(() => props.group && !props.mine && props.first)
const showAvatar = computed(() => props.group && !props.mine)
const resendable = computed(() => props.mine && props.canResend && props.message.status === 'failed' && props.message.kind === 'text' && !!props.message.text)
const pinnable = computed(() => props.canPin && !!props.message.server_msg_id)
const actions = usePopover()
function choose(action: 'pin' | 'unpin' | 'resend') { actions.close(); if (action === 'pin') emit('pin'); else if (action === 'unpin') emit('unpin'); else emit('resend') }
</script>

<template>
  <li v-if="message.kind === 'system'" class="chat-day chat-system"><span class="chat-chip">{{ message.text || '系统消息' }}</span></li>
  <li v-else class="chat-msg" :class="{ 'is-mine': mine, 'is-first': first, 'is-last': last, 'has-avatar': showAvatar }" :data-msg="message.server_msg_id ?? undefined">
    <span v-if="showAvatar" class="chat-msg-avatar"><ChatAvatar v-if="last" :name="sender" :seed="sender" size="sm" /></span>
    <div class="chat-bubble" :class="{ 'is-text': message.kind === 'text', 'is-media': message.kind === 'image' && !!url, 'is-card': message.kind === 'approval_card', 'has-state': message.status === 'failed' || message.status === 'unknown', 'has-pin': message.pinned }">
      <span class="sr-only">{{ mine ? '我' : sender }}</span>
      <p v-if="showName || mentionsMe" class="chat-bubble-name">
        <span v-if="showName" :class="`chat-name-${colorIndex(sender)}`" aria-hidden="true">{{ sender }}</span>
        <span v-if="mentionsMe" class="chat-tag is-accent">@ 了我</span>
      </p>
      <p v-if="message.kind === 'text'" class="chat-text preserve-text">{{ message.text }}<span class="chat-meta-space" aria-hidden="true"></span></p>
      <template v-else-if="message.kind === 'image'">
        <img v-if="url" :src="url" :alt="`${mine ? '我' : sender}发送的图片`" :width="message.image?.width ?? undefined" :height="message.image?.height ?? undefined" class="chat-image" />
        <!-- External image links are never loaded automatically (plan §4.4); show the address as text. -->
        <p v-else-if="message.image?.external_url" class="chat-text chat-note">外部图片未加载：<span class="preserve-text">{{ message.image.external_url }}</span></p>
        <p v-else-if="failed" class="chat-text chat-note">图片加载失败。</p>
        <p v-else class="chat-text chat-note" role="status">正在加载图片…</p>
      </template>
      <ApprovalCard v-else-if="message.kind === 'approval_card' && message.approval_execution_id" :scope="scope" :execution-id="message.approval_execution_id" :text="message.text" />
      <p v-else class="chat-text chat-note">暂不支持显示这类消息。</p>
      <span class="chat-meta">
        <Pin v-if="message.pinned" class="chat-meta-icon" aria-hidden="true" /><span v-if="message.pinned" class="sr-only">已置顶</span>
        <time :datetime="message.sent_at">{{ clockTime(message.sent_at) }}</time>
        <template v-if="mine || message.status !== 'sent'">
          <span v-if="message.status === 'sending'" class="chat-sendstate"><Clock class="chat-meta-icon" aria-hidden="true" /><span class="sr-only">发送中</span></span>
          <span v-else-if="message.status === 'failed'" class="chat-sendstate is-error"><CircleAlert class="chat-meta-icon" aria-hidden="true" />发送失败</span>
          <span v-else-if="message.status === 'unknown'" class="chat-sendstate is-warning"><CircleHelp class="chat-meta-icon" aria-hidden="true" />结果未知</span>
          <span v-else class="chat-sendstate"><Check class="chat-meta-icon" aria-hidden="true" /><span class="sr-only">已发送</span></span>
        </template>
      </span>
    </div>
    <div v-if="pinnable || resendable" :ref="(el) => { actions.root.value = el as HTMLElement | null }" class="chat-pop chat-msg-actions" :class="{ 'is-open': actions.open.value }">
      <button type="button" class="chat-icon-button is-small" aria-label="消息操作" aria-haspopup="menu" :aria-expanded="actions.open.value" :disabled="locked" @click="actions.toggle"><EllipsisVertical aria-hidden="true" /></button>
      <div v-if="actions.open.value" class="chat-menu" :class="mine ? 'chat-menu-right' : ''" role="menu" aria-label="消息操作">
        <button v-if="resendable" type="button" role="menuitem" class="chat-menu-item" @click="choose('resend')"><RotateCw aria-hidden="true" />重新发送</button>
        <button v-if="pinnable && !message.pinned" type="button" role="menuitem" class="chat-menu-item" @click="choose('pin')"><Pin aria-hidden="true" />置顶这条消息</button>
        <button v-if="pinnable && message.pinned" type="button" role="menuitem" class="chat-menu-item" @click="choose('unpin')"><PinOff aria-hidden="true" />取消置顶</button>
      </div>
    </div>
  </li>
</template>
