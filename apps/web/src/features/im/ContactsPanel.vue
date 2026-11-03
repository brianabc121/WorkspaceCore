<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowLeft, MessageSquare, Pencil, Plus, Trash2 } from '@lucide/vue'
import { dataOf, type Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { STATUS_LABELS, useContactGroups, useContacts } from './queries'
import { useImAction } from './action'
import ChatAvatar from './ChatAvatar.vue'

// 通讯录（消息页左栏，T-0095）：本工作空间的 OpenClaw 实例，按通讯录分组排列；不能删除好友（plan §4.4）。
const props = defineProps<{ scope: string; opening?: boolean; error?: string }>()
defineEmits<{ chat: [instanceId: string]; back: [] }>()
const auth = useAuthStore()
const contacts = useContacts(props.scope), groups = useContactGroups(props.scope)
const { busy, error: actionError, notice, act, header } = useImAction(props.scope)
const adding = ref(false), newName = ref(''), editing = ref<string | null>(null), rename = ref(''), picked = ref<string[]>([])
const instances = computed(() => (contacts.data.value?.items ?? []).filter(c => c.instance_id))
const sections = computed(() => {
  const all = groups.data.value?.items ?? []
  const grouped = all.map(g => ({ group: g, items: instances.value.filter(c => c.contact_group_ids.includes(g.id)) }))
  const rest = instances.value.filter(c => !c.contact_group_ids.some(id => all.some(g => g.id === id)))
  return { grouped, rest }
})

async function create() {
  const name = newName.value.trim()
  if (name && await act(() => auth.api.POST('/api/v1/im/contact-groups', { params: { header }, body: { name, sort_order: 0 } }), `已新建分组「${name}」`)) { newName.value = ''; adding.value = false }
}
function edit(group: Schema['ImContactGroupOut']) { editing.value = group.id; rename.value = group.name; picked.value = [...group.instance_ids] }
async function save(group: Schema['ImContactGroupOut']) {
  const name = rename.value.trim()
  const path = { contact_group_id: group.id }
  const ok = await act(async () => {
    let version = group.version
    if (name && name !== group.name) version = dataOf(await auth.api.PATCH('/api/v1/im/contact-groups/{contact_group_id}', { params: { header, path }, body: { name, version } })).version
    const same = picked.value.length === group.instance_ids.length && picked.value.every(id => group.instance_ids.includes(id))
    if (!same) await auth.api.PUT('/api/v1/im/contact-groups/{contact_group_id}/members', { params: { header, path }, body: { instance_ids: picked.value, version } })
  }, '分组已保存')
  if (ok) editing.value = null
}
function remove(group: Schema['ImContactGroupOut']) {
  if (!window.confirm(`确认删除分组「${group.name}」？分组里的实例不会被删除。`)) return
  void act(() => auth.api.DELETE('/api/v1/im/contact-groups/{contact_group_id}', { params: { header, path: { contact_group_id: group.id }, query: { version: group.version } } }), '分组已删除')
}
</script>

<template>
  <section aria-label="通讯录">
    <header class="chat-bar chat-side-header">
      <button type="button" class="chat-icon-button" aria-label="返回会话列表" @click="$emit('back')"><ArrowLeft aria-hidden="true" /></button>
      <h1 class="chat-side-title">通讯录</h1>
      <button type="button" class="chat-icon-button" :aria-expanded="adding" aria-label="新建分组" @click="adding = !adding"><Plus aria-hidden="true" /></button>
    </header>
    <div class="chat-list-scroll">
      <p class="chat-list-note">通讯录是本工作空间的 OpenClaw 实例，不能删除好友；停用的实例仍会显示。</p>
      <form v-if="adding" class="chat-inline-form" @submit.prevent="create">
        <label class="chat-field"><span>新建分组</span><input v-model="newName" maxlength="32" placeholder="例如：运维" /></label>
        <button type="submit" class="chat-button is-primary" :disabled="busy || !newName.trim()">新建</button>
      </form>
      <p v-if="contacts.isPending.value || groups.isPending.value" role="status" class="chat-list-note">正在加载通讯录…</p>
      <p v-else-if="contacts.error.value || groups.error.value" role="alert" class="chat-list-note">无法加载通讯录，请稍后重试。</p>
      <template v-else>
        <section v-for="{ group, items } in sections.grouped" :key="group.id" class="chat-contact-section" :aria-label="`分组：${group.name}`">
          <div class="chat-section-head">
            <h2>{{ group.name }}（{{ items.length }}）</h2>
            <button type="button" class="chat-icon-button is-small" :disabled="busy" aria-label="编辑分组" @click="edit(group)"><Pencil aria-hidden="true" /></button>
            <button type="button" class="chat-icon-button is-small" :disabled="busy" aria-label="删除分组" @click="remove(group)"><Trash2 aria-hidden="true" /></button>
          </div>
          <form v-if="editing === group.id" class="chat-stack-form" @submit.prevent="save(group)">
            <label class="chat-field"><span>分组名称</span><input v-model="rename" required maxlength="32" /></label>
            <fieldset class="chat-checks"><legend>分组里的实例</legend><label v-for="c in instances" :key="c.im_user_id" class="chat-check"><input v-model="picked" type="checkbox" :value="c.instance_id" /> {{ c.nickname }}</label></fieldset>
            <div class="chat-actions"><button type="submit" class="chat-button is-primary" :disabled="busy || !rename.trim()">保存分组</button><button type="button" class="chat-button" :disabled="busy" @click="editing = null">取消</button></div>
          </form>
          <p v-if="!items.length" class="chat-list-note">这个分组还没有实例。</p>
          <ul class="chat-contact-list">
            <li v-for="c in items" :key="c.im_user_id" class="chat-contact">
              <ChatAvatar :name="c.nickname" size="md" :status="c.status" />
              <span class="chat-contact-who"><span class="chat-contact-name">{{ c.nickname }}</span><span class="chat-contact-status" :class="`is-${c.status}`">实例 · {{ STATUS_LABELS[c.status] }}</span></span>
              <button type="button" class="chat-icon-button" :disabled="c.status === 'disabled' || opening" aria-label="发消息" @click="$emit('chat', c.instance_id)"><MessageSquare aria-hidden="true" /></button>
            </li>
          </ul>
        </section>
        <section class="chat-contact-section" aria-label="未分组">
          <div class="chat-section-head"><h2>未分组（{{ sections.rest.length }}）</h2></div>
          <p v-if="!instances.length" class="chat-list-note">还没有实例。到“OpenClaw 实例”页登记实例后，这里会自动出现。</p>
          <ul class="chat-contact-list">
            <li v-for="c in sections.rest" :key="c.im_user_id" class="chat-contact">
              <ChatAvatar :name="c.nickname" size="md" :status="c.status" />
              <span class="chat-contact-who"><span class="chat-contact-name">{{ c.nickname }}</span><span class="chat-contact-status" :class="`is-${c.status}`">实例 · {{ STATUS_LABELS[c.status] }}</span></span>
              <button type="button" class="chat-icon-button" :disabled="c.status === 'disabled' || opening" aria-label="发消息" @click="$emit('chat', c.instance_id)"><MessageSquare aria-hidden="true" /></button>
            </li>
          </ul>
        </section>
      </template>
      <p v-if="error" role="alert" class="chat-list-note is-error">{{ error }}</p>
      <p v-if="actionError" role="alert" class="chat-list-note is-error">{{ actionError }}</p>
      <p v-if="notice" role="status" class="chat-list-note">{{ notice }}</p>
    </div>
  </section>
</template>
