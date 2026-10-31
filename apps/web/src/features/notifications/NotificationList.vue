<script setup lang="ts">
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
defineProps<{ items: Schema['NotificationOut'][]; busy?: boolean; label: string }>()
defineEmits<{ read: [id: string] }>()
function internalPath(value: string | null | undefined) { return !!value && value.startsWith('/') && !value.startsWith('//') && ![...value].some(char => char === '\\' || char.charCodeAt(0) <= 32 || char.charCodeAt(0) === 127) }
</script>
<template>
  <section role="region" :aria-label="label" class="notification-list">
    <article v-for="item in items" :key="item.id" class="notification-item" :class="{ 'is-unread': !item.read_at }">
      <span class="status-badge">{{ { info: '信息', warning: '警告', error: '错误' }[item.severity] }}</span>
      <h3><RouterLink v-if="internalPath(item.link)" :to="item.link!">{{ item.title }}</RouterLink><span v-else>{{ item.title }}</span></h3>
      <p class="preserve-text">{{ item.body }}</p><time :datetime="item.created_at">{{ formatTime(item.created_at) }}</time>
      <button v-if="!item.read_at" class="secondary-button" :disabled="busy" :aria-label="`标为已读：${item.title}`" @click="$emit('read', item.id)">标为已读</button><span v-else class="form-help">已读</span>
    </article>
  </section>
</template>
