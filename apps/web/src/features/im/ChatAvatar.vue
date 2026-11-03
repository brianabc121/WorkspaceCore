<script setup lang="ts">
import { computed } from 'vue'
import { Bell } from '@lucide/vue'
import { colorIndex, initial } from './format'

// 圆形头像：名字的第一个字 + 按 ID 固定的颜色；通知会话用铃铛；停用的实例用灰色。
// 在线状态的小圆点只是装饰，文字说明由使用的地方提供（读屏软件不重复读）。
const props = withDefaults(defineProps<{ name: string; seed?: string; size?: 'sm' | 'md' | 'lg' | 'xl'; status?: 'pending' | 'online' | 'offline' | 'disabled' | null; notice?: boolean; self?: boolean }>(), { seed: '', size: 'lg', status: null, notice: false, self: false })
const tone = computed(() => props.self ? 'chat-avatar-self' : props.status === 'disabled' ? 'chat-avatar-off' : `chat-tone-${colorIndex(props.seed || props.name)}`)
</script>

<template>
  <span class="chat-avatar" :class="[`chat-avatar-${size}`, notice ? 'chat-avatar-notice' : tone]" aria-hidden="true">
    <Bell v-if="notice" class="chat-avatar-icon" />
    <template v-else>{{ initial(name) }}</template>
    <span v-if="status === 'online'" class="chat-avatar-online"></span>
  </span>
</template>
