<script setup lang="ts">
import { computed, toRef } from 'vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { useMediaUrl } from './media'

const props = defineProps<{ scope: string; message: Schema['ImMessageOut']; sender: string; mine: boolean; canPin: boolean; locked: boolean }>()
defineEmits<{ pin: []; unpin: [] }>()
const media = toRef(() => props.message.kind === 'image' ? props.message.image?.media_id ?? null : null)
const { url, failed } = useMediaUrl(props.scope, media)
const state = computed(() => ({ sending: '发送中', failed: '发送失败', unknown: '结果未知', sent: '' })[props.message.status])
</script>

<template>
  <li class="im-message" :class="{ 'im-message-mine': mine, 'im-message-system': message.kind === 'system' }">
    <p v-if="message.kind === 'system'" class="im-system-text">{{ message.text || '系统消息' }}</p>
    <template v-else>
      <p class="im-message-meta"><span class="im-sender">{{ mine ? '我' : sender }}</span> <time :datetime="message.sent_at">{{ formatTime(message.sent_at) }}</time><span v-if="state" class="badge">{{ state }}</span><span v-if="message.pinned" class="badge">已置顶</span></p>
      <div class="im-bubble">
        <p v-if="message.kind === 'text'" class="preserve-text">{{ message.text }}</p>
        <template v-else-if="message.kind === 'image'">
          <img v-if="url" :src="url" :alt="`${mine ? '我' : sender}发送的图片`" :width="message.image?.width ?? undefined" :height="message.image?.height ?? undefined" class="im-image" />
          <!-- External image links are never loaded automatically (plan §4.4); show the address as text. -->
          <p v-else-if="message.image?.external_url" class="note-text">外部图片未加载：<span class="preserve-text">{{ message.image.external_url }}</span></p>
          <p v-else-if="failed" class="note-text">图片加载失败。</p>
          <p v-else class="note-text" role="status">正在加载图片…</p>
        </template>
        <p v-else-if="message.kind === 'approval_card'" class="note-text">审批卡片：请到 AI 审批中心处理。</p>
        <p v-else class="note-text">暂不支持显示这类消息。</p>
      </div>
      <div v-if="canPin && message.server_msg_id" class="im-message-actions">
        <button v-if="!message.pinned" type="button" class="secondary-button" :disabled="locked" @click="$emit('pin')">置顶这条消息</button>
        <button v-else type="button" class="secondary-button" :disabled="locked" @click="$emit('unpin')">取消置顶</button>
      </div>
    </template>
  </li>
</template>
