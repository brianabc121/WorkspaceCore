<script setup lang="ts">
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import type { ConversationKind } from './queries'

defineProps<{ groups: Schema['ImContactGroupOut'][]; items: Schema['ImConversationOut'][]; selected: string | null; loading: boolean; error: string; hasMore: boolean; fetchingMore: boolean }>()
const filter = defineModel<{ kind: ConversationKind | ''; q: string; group: string }>('filter', { required: true })
defineEmits<{ select: [id: string]; more: [] }>()
const kinds: { value: ConversationKind | ''; label: string }[] = [{ value: '', label: '全部' }, { value: 'single', label: '单聊' }, { value: 'group', label: '群聊' }, { value: 'notice', label: '通知' }]
function preview(item: Schema['ImConversationOut']) {
  const last = item.last_message
  if (!last) return '暂无消息'
  return ({ text: last.text_preview ?? '', image: '[图片]', approval_card: '[审批卡片]', system: last.text_preview ?? '[系统消息]', unsupported: '[暂不支持的消息]' } as Record<string, string>)[last.kind] ?? ''
}
</script>

<template>
  <section class="panel im-conversations" aria-label="会话列表">
    <div class="im-filter" role="group" aria-label="会话类型">
      <button v-for="kind in kinds" :key="kind.value" type="button" class="secondary-button" :aria-pressed="filter.kind === kind.value" @click="filter = { ...filter, kind: kind.value }">{{ kind.label }}</button>
    </div>
    <label v-if="groups.length" class="form-field"><span>按分组</span><select :value="filter.group" @change="filter = { ...filter, group: ($event.target as HTMLSelectElement).value }"><option value="">全部分组</option><option v-for="g in groups" :key="g.id" :value="g.id">{{ g.name }}</option></select></label>
    <label class="form-field"><span>搜索会话</span><input :value="filter.q" type="search" maxlength="100" placeholder="名称" @change="filter = { ...filter, q: ($event.target as HTMLInputElement).value.trim() }" /></label>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-else-if="loading" role="status">正在加载会话…</p>
    <p v-else-if="!items.length" class="empty-state">暂无会话。到通讯录里选择一个实例开始聊天。</p>
    <ul v-else class="im-conversation-list">
      <li v-for="item in items" :key="item.conversation_id">
        <button type="button" class="im-conversation" :aria-current="selected === item.conversation_id ? 'true' : undefined" @click="$emit('select', item.conversation_id)">
          <span class="im-conversation-title">{{ item.title }}<span v-if="item.pinned" class="badge">置顶</span><span v-if="item.muted" class="badge">免打扰</span></span>
          <span class="im-conversation-preview">{{ preview(item) }}</span>
          <span class="im-conversation-side"><time v-if="item.last_message_at" :datetime="item.last_message_at">{{ formatTime(item.last_message_at) }}</time><span v-if="item.unread_count" class="im-unread" :class="{ 'im-unread-muted': item.muted }" :aria-label="`${item.unread_count} 条未读`">{{ item.unread_count > 99 ? '99+' : item.unread_count }}</span></span>
        </button>
      </li>
    </ul>
    <button v-if="hasMore" type="button" class="secondary-button" :disabled="fetchingMore" @click="$emit('more')">更多会话</button>
  </section>
</template>
