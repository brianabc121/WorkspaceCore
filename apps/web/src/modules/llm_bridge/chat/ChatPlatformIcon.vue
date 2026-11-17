<script setup lang="ts">
import { computed } from 'vue'
import { Asterisk, Bot, CodeXml, Hexagon, MessageSquare, Users } from '@lucide/vue'

// 平台的小图标（会话列表、对话里的头像、会话详情）：Claude 是星芒，CodeBuddy 是机器人，
// ChatGPT / Codex 的对话是六边形、开发会话是代码符号；多模型同问是一组人。只为一眼区分，不是各家的商标。
const props = defineProps<{ platform?: string | null; mode?: string | null; group?: boolean }>()
const icon = computed(() => {
  if (props.group) return Users
  if (props.platform === 'claude') return Asterisk
  if (props.platform === 'workbuddy') return Bot
  if (props.platform === 'codex' || props.platform === 'chatgpt') return props.mode === 'dev' ? CodeXml : Hexagon
  return MessageSquare
})
</script>

<template>
  <span class="lc-mark" :class="{ 'is-group': group }" :data-platform="platform || undefined" :data-mode="mode || undefined" aria-hidden="true"><component :is="icon" /></span>
</template>
