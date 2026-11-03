<script setup lang="ts">
import { nextTick, onScopeDispose, ref, watch } from 'vue'
import { ArrowLeft, BellOff, Bot, Menu, Pencil, Pin, Search, Users } from '@lucide/vue'
import type { Schema } from '../../services/api'
import type { ConversationKind, Folder } from './queries'
import { listTime } from './format'
import { usePopover } from './usePopover'
import ChatAvatar from './ChatAvatar.vue'

// 消息页左栏（T-0095）：菜单、搜索、分类、会话列表、右下角“新建”。
const props = defineProps<{ folders: Folder[]; items: Schema['ImConversationOut'][]; selected: string | null; selfId: string | null; names: Record<string, string>; statuses: Record<string, Schema['ImContactOut']['status']>; wide: boolean; loading: boolean; error: string; hasMore: boolean; fetchingMore: boolean }>()
const filter = defineModel<{ kind: ConversationKind | ''; q: string; group: string }>('filter', { required: true })
const emit = defineEmits<{ select: [id: string]; more: []; menu: []; contacts: []; create: [] }>()

// 搜索边输入边筛选（停顿 300 毫秒后再请求）
const query = ref(filter.value.q), searching = ref(!!filter.value.q), input = ref<HTMLInputElement | null>(null)
let timer: ReturnType<typeof setTimeout> | undefined
watch(query, value => {
  clearTimeout(timer)
  timer = setTimeout(() => { const q = value.trim().slice(0, 100); if (q !== filter.value.q) filter.value = { ...filter.value, q } }, 300)
})
onScopeDispose(() => clearTimeout(timer))
async function startSearch() { searching.value = true; await nextTick(); input.value?.focus() }
function stopSearch() { searching.value = false; query.value = '' }

const pick = (folder: Folder) => { filter.value = { ...filter.value, kind: folder.kind, group: folder.group } }
const active = (folder: Folder) => filter.value.kind === folder.kind && filter.value.group === folder.group

const kinds: Record<string, string> = { image: '[图片]', approval_card: '[审批卡片]', unsupported: '[暂不支持的消息]' }
function preview(item: Schema['ImConversationOut']) {
  const last = item.last_message
  if (!last) return '暂无消息'
  return kinds[last.kind] ?? (last.text_preview || (last.kind === 'system' ? '[系统消息]' : ''))
}
function sender(item: Schema['ImConversationOut']) {
  const last = item.last_message
  if (!last || last.kind === 'system') return ''
  if (last.sender_im_id === props.selfId) return '你：'
  return item.kind === 'group' && props.names[last.sender_im_id] ? `${props.names[last.sender_im_id]}：` : ''
}
const peer = (item: Schema['ImConversationOut']) => item.peer_instance_id ? props.statuses[item.peer_instance_id] ?? null : null

const fab = usePopover()
function choose(action: 'contacts' | 'create') { fab.close(); if (action === 'contacts') emit('contacts'); else emit('create') }
</script>

<template>
  <section aria-label="会话列表">
    <header class="chat-bar chat-side-header">
      <template v-if="wide || !searching">
        <button type="button" class="chat-icon-button" aria-label="菜单" @click="$emit('menu')"><Menu aria-hidden="true" /></button>
        <h1 class="chat-side-title" :class="{ 'sr-only': wide }">消息</h1>
        <label v-if="wide" class="chat-search">
          <span class="sr-only">搜索会话</span>
          <Search class="chat-search-icon" aria-hidden="true" />
          <input v-model="query" type="search" maxlength="100" placeholder="搜索" />
        </label>
        <button v-else type="button" class="chat-icon-button" aria-label="搜索会话" @click="startSearch"><Search aria-hidden="true" /></button>
      </template>
      <template v-else>
        <button type="button" class="chat-icon-button" aria-label="结束搜索" @click="stopSearch"><ArrowLeft aria-hidden="true" /></button>
        <label class="chat-search">
          <span class="sr-only">搜索会话</span>
          <input ref="input" v-model="query" type="search" maxlength="100" placeholder="搜索" />
        </label>
      </template>
    </header>

    <div class="chat-folders" role="group" aria-label="会话分类">
      <button v-for="folder in folders" :key="folder.id" type="button" class="chat-folder" :aria-pressed="active(folder)" @click="pick(folder)">
        {{ folder.label }}<span v-if="folder.count" class="chat-badge" :class="{ 'is-muted': folder.muted }" :aria-label="`${folder.count} 个会话有未读`">{{ folder.count > 99 ? '99+' : folder.count }}</span>
      </button>
    </div>

    <div class="chat-list-scroll">
      <p v-if="error" role="alert" class="chat-list-note">{{ error }}</p>
      <p v-else-if="loading" role="status" class="chat-list-note">正在加载会话…</p>
      <p v-else-if="!items.length" class="chat-list-note">{{ filter.q ? '没有找到匹配的会话。' : '暂无会话。点右下角的按钮，与实例聊天或新建群聊。' }}</p>
      <ul v-else class="chat-list">
        <li v-for="item in items" :key="item.conversation_id">
          <button type="button" class="chat-row" :aria-current="selected === item.conversation_id ? 'true' : undefined" @click="$emit('select', item.conversation_id)">
            <ChatAvatar :name="item.title" :notice="item.kind === 'notice'" :status="peer(item)" />
            <span class="chat-row-body">
              <span class="chat-row-line">
                <Users v-if="item.kind === 'group'" class="chat-row-kind" aria-hidden="true" />
                <Bot v-else-if="item.kind === 'single'" class="chat-row-kind" aria-hidden="true" />
                <span class="chat-row-title">{{ item.title }}</span>
                <span v-if="peer(item) === 'online'" class="sr-only">在线</span>
                <span v-if="peer(item) === 'disabled'" class="chat-tag">已停用</span>
                <BellOff v-if="item.muted" class="chat-row-flag" aria-hidden="true" /><span v-if="item.muted" class="sr-only">免打扰</span>
                <time v-if="item.last_message_at" class="chat-row-time" :datetime="item.last_message_at">{{ listTime(item.last_message_at) }}</time>
              </span>
              <span class="chat-row-line">
                <span class="chat-row-preview"><span v-if="sender(item)" class="chat-row-sender">{{ sender(item) }}</span>{{ preview(item) }}</span>
                <span v-if="item.unread_count" class="chat-badge" :class="{ 'is-muted': item.muted }" :aria-label="`${item.unread_count} 条未读`">{{ item.unread_count > 99 ? '99+' : item.unread_count }}</span>
                <template v-else-if="item.pinned"><Pin class="chat-row-flag" aria-hidden="true" /><span class="sr-only">已置顶</span></template>
              </span>
            </span>
          </button>
        </li>
      </ul>
      <button v-if="hasMore" type="button" class="chat-more" :disabled="fetchingMore" @click="$emit('more')">{{ fetchingMore ? '正在加载…' : '更多会话' }}</button>
    </div>

    <div :ref="(el) => { fab.root.value = el as HTMLElement | null }" class="chat-fab-wrap">
      <div v-if="fab.open.value" class="chat-menu chat-fab-menu" role="menu" aria-label="新建">
        <button type="button" role="menuitem" class="chat-menu-item" @click="choose('contacts')"><Bot aria-hidden="true" />与实例聊天</button>
        <button type="button" role="menuitem" class="chat-menu-item" @click="choose('create')"><Users aria-hidden="true" />新建群聊</button>
      </div>
      <button type="button" class="chat-fab" aria-haspopup="menu" :aria-expanded="fab.open.value" aria-label="新建：与实例聊天或新建群聊" @click="fab.toggle"><Pencil aria-hidden="true" /></button>
    </div>
  </section>
</template>
