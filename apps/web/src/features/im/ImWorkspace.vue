<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import { imError, useContactGroups, useContacts, useConversations, useImStatus, type ConversationKind, type Folder } from './queries'
import { useMediaQuery } from '../../composables/useMediaQuery'
import { useCreateIntent } from '../../composables/useCreateIntent'
import ConversationList from './ConversationList.vue'
import ChatWindow from './ChatWindow.vue'
import ChatInfo from './ChatInfo.vue'
import ContactsPanel from './ContactsPanel.vue'
import GroupCreateForm from './GroupCreateForm.vue'
import ImMenu from './ImMenu.vue'

// 消息页（T-0095）：独立页面，聊天应用式布局。地址里的参数决定显示什么，刷新、后退都能回到同一处：
//   c=<会话>   选中的会话；view=contacts 左栏显示通讯录；info=1 打开会话信息。
// 宽屏三栏（会话列表 / 聊天 / 会话信息），中等宽度信息栏盖在聊天上，手机一次只显示一栏（CSS 按 data-pane 切换）。
const auth = useAuthStore(), route = useRoute(), router = useRouter(), cache = useQueryClient()
const scope = auth.workspaceId!, user = auth.user?.id
const wide = useMediaQuery('(min-width: 768px)')

type Filter = { kind: ConversationKind | ''; q: string; group: string }
const filter = ref<Filter>({ kind: '', q: '', group: '' })
const everything = ref<Filter>({ kind: '', q: '', group: '' })
const status = useImStatus(scope), conversations = useConversations(scope, filter), all = useConversations(scope, everything)
const contacts = useContacts(scope), contactGroups = useContactGroups(scope)
const ready = computed(() => !!(status.data.value?.available && status.data.value.account_ready))
const selfId = computed(() => status.data.value?.im_user_id ?? null)

const view = computed(() => route.query.view === 'contacts' ? 'contacts' : 'chats')
const selectedId = computed(() => typeof route.query.c === 'string' ? route.query.c : null)
const infoOpen = computed(() => route.query.info === '1')
const items = computed(() => [...new Map((conversations.data.value?.pages ?? []).flatMap(p => p.items).map(c => [c.conversation_id, c])).values()])
const allItems = computed(() => (all.data.value?.pages ?? []).flatMap(p => p.items))
// 选中的会话可能不在当前筛选结果里：记住最后一次看到的那份
const remembered = ref<Schema['ImConversationOut'] | null>(null)
const selected = computed(() => {
  const id = selectedId.value
  return items.value.find(c => c.conversation_id === id) ?? allItems.value.find(c => c.conversation_id === id) ?? (remembered.value?.conversation_id === id ? remembered.value : null)
})
watch(selected, value => { if (value) remembered.value = value })
const pane = computed(() => selected.value ? (infoOpen.value ? 'info' : 'chat') : 'list')

const contactItems = computed(() => contacts.data.value?.items ?? [])
const names = computed<Record<string, string>>(() => Object.fromEntries(contactItems.value.map(c => [c.im_user_id, c.nickname])))
const statusByInstance = computed<Record<string, Schema['ImContactOut']['status']>>(() => Object.fromEntries(contactItems.value.map(c => [c.instance_id, c.status])))
const peerStatus = computed(() => selected.value?.peer_instance_id ? statusByInstance.value[selected.value.peer_instance_id] ?? null : null)

// 顶部分类：会话类型 + 通讯录分组。未读数按“有未读的会话”计数，只在会话都加载完时显示（接口只给总未读数）
const counted = computed(() => !all.hasNextPage.value && !!all.data.value)
function unread(list: Schema['ImConversationOut'][]) {
  const loud = list.filter(c => c.unread_count && !c.muted).length
  return loud ? { count: loud, muted: false } : { count: list.filter(c => c.unread_count).length, muted: true }
}
const folders = computed<Folder[]>(() => {
  const list = allItems.value, count = (pick: (c: Schema['ImConversationOut']) => boolean) => counted.value ? unread(list.filter(pick)) : { count: 0, muted: false }
  const kinds: Folder[] = [
    { id: 'all', label: '全部', kind: '', group: '', ...count(() => true) },
    { id: 'single', label: '单聊', kind: 'single', group: '', ...count(c => c.kind === 'single') },
    { id: 'group', label: '群聊', kind: 'group', group: '', ...count(c => c.kind === 'group') },
    { id: 'notice', label: '通知', kind: 'notice', group: '', ...count(c => c.kind === 'notice') },
  ]
  const groups = (contactGroups.data.value?.items ?? []).map(g => ({ id: `cg:${g.id}`, label: g.name, kind: '' as const, group: g.id, ...count(c => !!c.peer_instance_id && g.instance_ids.includes(c.peer_instance_id)) }))
  return [...kinds, ...groups]
})

// 手机上进入下一层用 push（系统返回键回到上一层），宽屏用 replace（不堆历史）。push 时在历史记录里记下上一层的地址，
// 页面上的“返回”只在上一条历史确实是这一层的上一层时才后退；从别的页面直接打开会话链接、或刚建群（replace）时，
// 改为在消息页里回到上一层，不会离开消息页。
const PARENT = 'imParent'
function go(query: LocationQueryRaw, deeper = false) {
  const location = { query: { ...route.query, ...query } }
  void (deeper && !wide.value ? router.push({ ...location, state: { [PARENT]: route.fullPath } }) : router.replace(location))
}
function back(query: LocationQueryRaw) {
  const state = router.options.history.state
  if (!wide.value && typeof state[PARENT] === 'string' && state[PARENT] === state.back) router.back()
  else go(query)
}
const select = (id: string) => go({ view: undefined, c: id, info: undefined }, true)
const toggleInfo = () => infoOpen.value ? back({ info: undefined }) : go({ info: '1' }, true)
const showContacts = () => go({ view: 'contacts' }, true)

const creating = ref(false), menuOpen = ref(false)
// 顶栏“创建 → 新建群聊”（规范 §8.1）：IM 可用时打开建群表单
useCreateIntent(() => status.isPending.value ? 'wait' : ready.value, () => { creating.value = true })
const refresh = () => cache.invalidateQueries({ predicate: q => q.queryKey[0] === 'im' && q.queryKey[1] === user && q.queryKey[2] === scope })
// 刷新列表期间用户可能已经点开了新群（甚至它的群信息）：那时不再跳转，免得把群信息栏关掉
async function created(conversationId: string) {
  creating.value = false
  const before = route.fullPath
  await refresh()
  if (route.fullPath === before) go({ view: undefined, c: conversationId, info: undefined })
}

const opening = ref(false), openError = ref('')
async function open(instanceId: string) {
  if (!instanceId || opening.value) return
  opening.value = true; openError.value = ''
  try {
    const conversation = dataOf(await auth.api.POST('/api/v1/im/conversations', { params: { header: { 'X-Workspace-Id': scope } }, body: { instance_id: instanceId } }))
    if (auth.user?.id !== user || auth.workspaceId !== scope) return
    remembered.value = conversation
    await refresh()
    go({ view: undefined, c: conversation.conversation_id, info: undefined })
  } catch (cause) { openError.value = imError(cause) }
  finally { opening.value = false }
}
</script>

<template>
  <div v-if="!ready" class="chat-app chat-app-state">
    <div class="chat-state">
      <h1>消息</h1>
      <p v-if="status.isPending.value" role="status">正在连接 IM…</p>
      <p v-else-if="status.error.value" role="alert">{{ imError(status.error.value) }}</p>
      <p v-else-if="!status.data.value?.available">还没有部署 OpenIM，IM 暂不可用。部署步骤见 docs/deployment.md 第 17 节。</p>
      <p v-else>你的 IM 账号正在准备，请稍后刷新。</p>
      <RouterLink to="/" class="chat-state-link">返回工作台</RouterLink>
    </div>
  </div>
  <div v-else class="chat-app" :data-pane="pane" :data-info="selected && infoOpen ? 'open' : undefined">
    <ContactsPanel v-if="view === 'contacts'" class="chat-sidebar" :scope="scope" :opening="opening" :error="openError" @chat="open" @back="back({ view: undefined })" />
    <ConversationList
      v-else v-model:filter="filter" class="chat-sidebar" :folders="folders" :items="items" :selected="selectedId" :self-id="selfId" :names="names" :statuses="statusByInstance" :wide="wide"
      :loading="conversations.isPending.value" :error="conversations.error.value ? imError(conversations.error.value) : ''" :has-more="!!conversations.hasNextPage.value" :fetching-more="conversations.isFetchingNextPage.value"
      @select="select" @more="conversations.fetchNextPage()" @menu="menuOpen = true" @contacts="showContacts" @create="creating = true"
    />
    <ChatWindow v-if="selected" :key="selected.conversation_id" class="chat-main" :scope="scope" :conversation="selected" :self-id="selfId" :names="names" :peer-status="peerStatus" :info-open="infoOpen" @back="back({ c: undefined, info: undefined })" @info="toggleInfo" />
    <section v-else class="chat-main chat-main-empty" aria-label="会话"><p class="chat-chip">选择一个会话开始聊天</p></section>
    <ChatInfo v-if="selected && infoOpen" :key="`info:${selected.conversation_id}`" class="chat-info" :scope="scope" :conversation="selected" :peer-status="peerStatus" @close="back({ info: undefined })" />
    <ImMenu v-model:open="menuOpen" @contacts="showContacts" @create="creating = true" />
    <GroupCreateForm v-if="creating" :scope="scope" @close="creating = false" @created="created" />
  </div>
</template>
