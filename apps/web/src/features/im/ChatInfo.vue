<script setup lang="ts">
import { computed, toRef } from 'vue'
import { ArrowLeft, Bell, BellOff, Download, Pin, X } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { STATUS_LABELS, useGroup } from './queries'
import { useConversationActions } from './action'
import ChatAvatar from './ChatAvatar.vue'
import GroupDetails from './GroupDetails.vue'

// 会话信息栏（T-0095）：宽屏在聊天右侧，中等宽度盖在聊天上，手机上占满一屏。
// 置顶、免打扰、导出对所有会话可用；群聊另有群公告、成员与防刷屏（GroupDetails）。
const props = defineProps<{ scope: string; conversation: Schema['ImConversationOut']; peerStatus: Schema['ImContactOut']['status'] | null }>()
defineEmits<{ close: [] }>()
const id = toRef(() => props.conversation.conversation_id)
const { busy, error, notice, setFlag, exportConversation } = useConversationActions(props.scope, () => id.value)
const isGroup = computed(() => props.conversation.kind === 'group' && !!props.conversation.group_id)
const group = useGroup(props.scope, toRef(() => isGroup.value ? props.conversation.group_id : null))
const title = computed(() => isGroup.value ? '群信息' : '会话信息')
const subtitle = computed(() => {
  if (props.conversation.kind === 'notice') return '系统通知，只能查看'
  if (props.conversation.kind === 'single') return props.peerStatus ? `OpenClaw 实例 · ${STATUS_LABELS[props.peerStatus]}` : 'OpenClaw 实例'
  const members = group.data.value?.members ?? []
  const online = members.filter(m => m.instance_id && m.online).length
  return group.data.value ? `${members.length} 名成员${online ? `，${online} 个实例在线` : ''}` : '群聊'
})
</script>

<template>
  <section :aria-label="title">
    <header class="chat-bar chat-info-head">
      <button type="button" class="chat-icon-button chat-info-back" :aria-label="`关闭${title}`" @click="$emit('close')"><ArrowLeft aria-hidden="true" /></button>
      <h2>{{ title }}</h2>
      <button type="button" class="chat-icon-button chat-info-close" :aria-label="`关闭${title}`" @click="$emit('close')"><X aria-hidden="true" /></button>
    </header>
    <div class="chat-info-scroll">
      <div class="chat-profile">
        <ChatAvatar :name="conversation.title" size="xl" :notice="conversation.kind === 'notice'" :status="peerStatus" />
        <p class="chat-profile-name">{{ conversation.title }}</p>
        <p class="chat-profile-sub">{{ subtitle }}</p>
      </div>
      <div class="chat-quick">
        <button type="button" class="chat-quick-item" :aria-pressed="conversation.muted" :disabled="busy" @click="setFlag({ muted: !conversation.muted }, conversation.muted ? '已关闭免打扰' : '已开启免打扰')"><BellOff v-if="conversation.muted" aria-hidden="true" /><Bell v-else aria-hidden="true" />免打扰</button>
        <button type="button" class="chat-quick-item" :aria-pressed="conversation.pinned" :disabled="busy" @click="setFlag({ pinned: !conversation.pinned }, conversation.pinned ? '已取消置顶会话' : '已置顶会话')"><Pin aria-hidden="true" />{{ conversation.pinned ? '已置顶' : '置顶' }}</button>
        <button type="button" class="chat-quick-item" :disabled="busy" @click="exportConversation"><Download aria-hidden="true" />导出</button>
      </div>
      <p v-if="error" role="alert" class="chat-info-note is-error">{{ error }}</p>
      <p v-if="notice" role="status" class="chat-info-note">{{ notice }}</p>
      <GroupDetails v-if="isGroup" :scope="scope" :group-id="conversation.group_id!" />
    </div>
  </section>
</template>
