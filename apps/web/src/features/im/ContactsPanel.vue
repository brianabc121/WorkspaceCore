<script setup lang="ts">
import { computed, ref } from 'vue'
import { dataOf, type Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { STATUS_LABELS, useContactGroups, useContacts } from './queries'
import { useImAction } from './action'

// Contacts are the workspace's instances; there is no way to delete a friend (plan §4.4).
const props = defineProps<{ scope: string }>()
defineEmits<{ chat: [instanceId: string] }>()
const auth = useAuthStore()
const contacts = useContacts(props.scope), groups = useContactGroups(props.scope)
const { busy, error, notice, act, header } = useImAction(props.scope)
const newName = ref(''), editing = ref<string | null>(null), rename = ref(''), picked = ref<string[]>([])
const instances = computed(() => (contacts.data.value?.items ?? []).filter(c => c.instance_id))
const sections = computed(() => {
  const all = groups.data.value?.items ?? []
  const grouped = all.map(g => ({ group: g, items: instances.value.filter(c => c.contact_group_ids.includes(g.id)) }))
  const rest = instances.value.filter(c => !c.contact_group_ids.some(id => all.some(g => g.id === id)))
  return { grouped, rest }
})

async function create() {
  const name = newName.value.trim()
  if (name && await act(() => auth.api.POST('/api/v1/im/contact-groups', { params: { header }, body: { name, sort_order: 0 } }), `已新建分组「${name}」`)) newName.value = ''
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
  <section class="panel im-contacts" aria-label="通讯录">
    <h2>通讯录</h2>
    <p class="form-help">通讯录是本工作空间的 OpenClaw 实例，不能删除好友；停用的实例仍会显示。</p>
    <p v-if="contacts.isPending.value || groups.isPending.value" role="status">正在加载通讯录…</p>
    <p v-else-if="contacts.error.value || groups.error.value" role="alert">无法加载通讯录，请稍后重试。</p>
    <template v-else>
      <form class="im-group-add" @submit.prevent="create">
        <label class="form-field"><span>新建分组</span><input v-model="newName" maxlength="32" placeholder="例如：运维" /></label>
        <button type="submit" class="secondary-button" :disabled="busy || !newName.trim()">新建</button>
      </form>
      <section v-for="{ group, items } in sections.grouped" :key="group.id" class="im-contact-section" :aria-label="`分组：${group.name}`">
        <div class="section-heading">
          <h3>{{ group.name }}（{{ items.length }}）</h3>
          <div class="form-actions"><button type="button" class="secondary-button" :disabled="busy" @click="edit(group)">编辑分组</button><button type="button" class="secondary-button" :disabled="busy" @click="remove(group)">删除分组</button></div>
        </div>
        <form v-if="editing === group.id" class="im-group-form" @submit.prevent="save(group)">
          <label class="form-field"><span>分组名称</span><input v-model="rename" required maxlength="32" /></label>
          <fieldset class="im-checks"><legend>分组里的实例</legend><label v-for="c in instances" :key="c.im_user_id" class="check-field"><input v-model="picked" type="checkbox" :value="c.instance_id" /> {{ c.nickname }}</label></fieldset>
          <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy || !rename.trim()">保存分组</button><button type="button" class="secondary-button" :disabled="busy" @click="editing = null">取消</button></div>
        </form>
        <ul class="im-contact-list"><li v-for="c in items" :key="c.im_user_id"><span>{{ c.nickname }} <span class="badge">{{ STATUS_LABELS[c.status] }}</span></span><button type="button" class="secondary-button" :disabled="c.status === 'disabled'" @click="$emit('chat', c.instance_id!)">发消息</button></li></ul>
      </section>
      <section class="im-contact-section" aria-label="未分组">
        <h3>未分组（{{ sections.rest.length }}）</h3>
        <p v-if="!instances.length" class="empty-state">还没有实例。到“OpenClaw 实例”页登记实例后，这里会自动出现。</p>
        <ul class="im-contact-list"><li v-for="c in sections.rest" :key="c.im_user_id"><span>{{ c.nickname }} <span class="badge">{{ STATUS_LABELS[c.status] }}</span></span><button type="button" class="secondary-button" :disabled="c.status === 'disabled'" @click="$emit('chat', c.instance_id!)">发消息</button></li></ul>
      </section>
    </template>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
  </section>
</template>
