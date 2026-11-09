<script setup lang="ts">
import { Laptop } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'

// 一个登录会话（T-0193）：接口只有 ID、创建 / 最近活动 / 到期时间与是否当前会话，
// 所以这里只显示这些，不推测设备、系统、位置或在线状态
const props = defineProps<{ session: Schema['SessionOut']; busy: boolean }>()
const emit = defineEmits<{ revoke: [session: Schema['SessionOut'], event: Event] }>()
const tag = (id: string) => id.replace(/-/g, '').slice(-4).toUpperCase()
</script>
<template>
  <li class="sc-session" :class="{ 'is-current': props.session.current }" :aria-label="props.session.current ? '当前会话' : `其他会话 ${tag(props.session.id)}`">
    <span class="sc-icon" aria-hidden="true"><Laptop /></span>
    <div class="sc-session-body">
      <p class="sc-session-head"><strong>{{ props.session.current ? '当前会话' : '其他会话' }}</strong><span class="asv-sub">会话 {{ tag(props.session.id) }}</span><span v-if="props.session.current" class="asv-state tone-green">当前浏览器</span></p>
      <dl class="sc-times">
        <div><dt>创建时间</dt><dd>{{ formatTime(props.session.created_at) }}</dd></div>
        <div><dt>最近活动</dt><dd>{{ props.session.last_seen_at ? formatTime(props.session.last_seen_at) : '没有记录' }}</dd></div>
        <div><dt>到期时间</dt><dd>{{ formatTime(props.session.expires_at) }}</dd></div>
      </dl>
    </div>
    <button type="button" class="secondary-button" :disabled="busy" @click="emit('revoke', props.session, $event)">{{ props.session.current ? '撤销当前会话' : '撤销会话' }}</button>
  </li>
</template>
