<script setup lang="ts">
import { computed, reactive, ref, toRef, watch } from 'vue'
import type { Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { useContacts, useGroup } from './queries'
import { useImAction } from './action'

const props = defineProps<{ scope: string; groupId: string }>()
const emit = defineEmits<{ close: [] }>()
const auth = useAuthStore()
const group = useGroup(props.scope, toRef(() => props.groupId)), contacts = useContacts(props.scope)
const { busy, error, notice, act, header } = useImAction(props.scope)
const info = reactive({ name: '', announcement: '' }), policy = reactive({ relay_enabled: true, max_agent_streak: 8, max_per_minute: 6 }), adding = ref('')
watch(() => group.data.value, g => {
  if (!g) return
  Object.assign(info, { name: g.name, announcement: g.announcement })
  Object.assign(policy, { relay_enabled: g.policy.relay_enabled, max_agent_streak: g.policy.max_agent_streak, max_per_minute: g.policy.max_per_minute })
}, { immediate: true })
const members = computed(() => group.data.value?.members ?? [])
const addable = computed(() => (contacts.data.value?.items ?? []).filter(c => c.instance_id && c.status !== 'disabled' && !members.value.some(m => m.im_user_id === c.im_user_id)))
const path = () => ({ group_id: props.groupId })
const saveInfo = () => act(() => auth.api.PATCH('/api/v1/im/groups/{group_id}', { params: { header, path: path() }, body: { name: info.name.trim(), announcement: info.announcement.trim() } }), '群资料已保存')
const savePolicy = () => act(() => auth.api.PUT('/api/v1/im/groups/{group_id}/policy', { params: { header, path: path() }, body: { ...policy, version: group.data.value!.policy.version } }), '防刷屏设置已保存')
async function add() {
  const instance = adding.value
  if (!instance) return
  if (await act(() => auth.api.POST('/api/v1/im/groups/{group_id}/members', { params: { header, path: path() }, body: { instance_ids: [instance] } }), '已拉入群')) adding.value = ''
}
function remove(member: Schema['ImGroupMemberOut']) {
  if (!window.confirm(`确认把「${member.nickname}」移出群？`)) return
  void act(() => auth.api.DELETE('/api/v1/im/groups/{group_id}/members/{im_user_id}', { params: { header, path: { ...path(), im_user_id: member.im_user_id } } }), '已移出群')
}
async function dismiss() {
  if (!window.confirm(`确认解散「${group.data.value?.name}」？解散后不能再发消息，聊天记录会保留。`)) return
  if (await act(() => auth.api.DELETE('/api/v1/im/groups/{group_id}', { params: { header, path: path() } }), '群已解散')) emit('close')
}
</script>

<template>
  <section class="im-group" aria-label="群设置">
    <div class="section-heading"><h3>群设置</h3><button type="button" class="secondary-button" @click="$emit('close')">收起</button></div>
    <p v-if="group.isPending.value" role="status">正在加载群信息…</p>
    <p v-else-if="group.error.value" role="alert">无法加载群信息，请稍后重试。</p>
    <template v-else-if="group.data.value">
      <p v-if="group.data.value.dismissed" class="feedback">这个群已解散，只能查看记录。</p>
      <p v-if="group.data.value.policy.paused" class="feedback">自动接力已暂停：实例连续发言达到上限，管理员发言后恢复。</p>
      <form class="im-group-form" @submit.prevent="saveInfo">
        <label class="form-field"><span>群名称</span><input v-model="info.name" required maxlength="64" :disabled="group.data.value.dismissed" /></label>
        <label class="form-field"><span>群公告</span><textarea v-model="info.announcement" rows="2" maxlength="1000" :disabled="group.data.value.dismissed" /></label>
        <button type="submit" class="secondary-button" :disabled="busy || group.data.value.dismissed || !info.name.trim()">保存群资料</button>
      </form>
      <h4>成员（{{ members.length }}）</h4>
      <ul class="im-group-members">
        <li v-for="member in members" :key="member.im_user_id">
          <span>{{ member.nickname }}<span v-if="member.role === 'owner'" class="badge">群主</span><span v-if="member.instance_id" class="badge">实例</span><span v-if="member.online !== null" class="badge">{{ member.online ? '在线' : '离线' }}</span></span>
          <button v-if="member.role !== 'owner' && !group.data.value.dismissed" type="button" class="secondary-button" :disabled="busy" @click="remove(member)">移出</button>
        </li>
      </ul>
      <form v-if="!group.data.value.dismissed" class="im-group-add" @submit.prevent="add">
        <label class="form-field"><span>拉入实例</span><select v-model="adding" :disabled="busy || !addable.length"><option value="">{{ addable.length ? '选择实例' : '没有可拉入的实例' }}</option><option v-for="c in addable" :key="c.im_user_id" :value="c.instance_id!">{{ c.nickname }}</option></select></label>
        <button type="submit" class="secondary-button" :disabled="busy || !adding">拉入</button>
      </form>
      <form v-if="!group.data.value.dismissed" class="im-group-form" @submit.prevent="savePolicy">
        <h4>防刷屏</h4>
        <label class="check-field"><input v-model="policy.relay_enabled" type="checkbox" /> 允许实例之间接力（关闭后实例 @ 其他实例的消息会被拒收）</label>
        <label class="form-field"><span>管理员不发言时，实例最多连续发言（条）</span><input v-model.number="policy.max_agent_streak" type="number" min="1" max="50" required /></label>
        <label class="form-field"><span>单个实例每分钟最多（条）</span><input v-model.number="policy.max_per_minute" type="number" min="1" max="60" required /></label>
        <button type="submit" class="secondary-button" :disabled="busy">保存防刷屏设置</button>
      </form>
      <button v-if="!group.data.value.dismissed" type="button" class="secondary-button im-danger" :disabled="busy" @click="dismiss">解散群</button>
    </template>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
  </section>
</template>
