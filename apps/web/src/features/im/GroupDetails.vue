<script setup lang="ts">
import { computed, reactive, ref, toRef, watch } from 'vue'
import { Megaphone, Pencil, UserPlus } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { useContacts, useGroup } from './queries'
import { useImAction } from './action'
import ChatAvatar from './ChatAvatar.vue'

// 群信息栏里的群部分（T-0095）：群公告与群名称、成员（拉入 / 移出）、防刷屏、解散群。
const props = defineProps<{ scope: string; groupId: string }>()
const auth = useAuthStore()
const group = useGroup(props.scope, toRef(() => props.groupId)), contacts = useContacts(props.scope)
const { busy, error, notice, act, header } = useImAction(props.scope)
const info = reactive({ name: '', announcement: '' }), policy = reactive({ relay_enabled: true, max_agent_streak: 8, max_per_minute: 6 }), adding = ref(''), editing = ref(false), inviting = ref(false)
watch(() => group.data.value, g => {
  if (!g) return
  Object.assign(info, { name: g.name, announcement: g.announcement })
  Object.assign(policy, { relay_enabled: g.policy.relay_enabled, max_agent_streak: g.policy.max_agent_streak, max_per_minute: g.policy.max_per_minute })
}, { immediate: true })
const members = computed(() => group.data.value?.members ?? [])
const addable = computed(() => (contacts.data.value?.items ?? []).filter(c => c.instance_id && c.status !== 'disabled' && !members.value.some(m => m.im_user_id === c.im_user_id)))
const path = () => ({ group_id: props.groupId })
async function saveInfo() {
  if (await act(() => auth.api.PATCH('/api/v1/im/groups/{group_id}', { params: { header, path: path() }, body: { name: info.name.trim(), announcement: info.announcement.trim() } }), '群资料已保存')) editing.value = false
}
function cancelEdit() { editing.value = false; if (group.data.value) Object.assign(info, { name: group.data.value.name, announcement: group.data.value.announcement }) }
const savePolicy = () => act(() => auth.api.PUT('/api/v1/im/groups/{group_id}/policy', { params: { header, path: path() }, body: { ...policy, version: group.data.value!.policy.version } }), '防刷屏设置已保存')
async function add() {
  const instance = adding.value
  if (!instance) return
  if (await act(() => auth.api.POST('/api/v1/im/groups/{group_id}/members', { params: { header, path: path() }, body: { instance_ids: [instance] } }), '已拉入群')) { adding.value = ''; inviting.value = false }
}
function remove(member: Schema['ImGroupMemberOut']) {
  if (!window.confirm(`确认把「${member.nickname}」移出群？`)) return
  void act(() => auth.api.DELETE('/api/v1/im/groups/{group_id}/members/{im_user_id}', { params: { header, path: { ...path(), im_user_id: member.im_user_id } } }), '已移出群')
}
function dismiss() {
  if (!window.confirm(`确认解散「${group.data.value?.name}」？解散后不能再发消息，聊天记录会保留。`)) return
  void act(() => auth.api.DELETE('/api/v1/im/groups/{group_id}', { params: { header, path: path() } }), '群已解散')
}
const memberLine = (m: Schema['ImGroupMemberOut']) => m.instance_id ? `实例 · ${m.online ? '在线' : '离线'}` : m.role === 'owner' ? '工作台账号 · 群主' : '工作台账号'
</script>

<template>
  <div class="chat-group">
    <p v-if="group.isPending.value" role="status" class="chat-info-note">正在加载群信息…</p>
    <p v-else-if="group.error.value" role="alert" class="chat-info-note is-error">无法加载群信息，请稍后重试。</p>
    <template v-else-if="group.data.value">
      <p v-if="group.data.value.dismissed" class="chat-info-note is-warning">这个群已解散，只能查看记录。</p>
      <p v-if="group.data.value.policy.paused" class="chat-info-note is-warning">自动接力已暂停：实例连续发言达到上限，管理员发言后恢复。</p>

      <section class="chat-info-section" aria-label="群公告">
        <form v-if="editing" class="chat-stack-form" @submit.prevent="saveInfo">
          <label class="chat-field"><span>群名称</span><input v-model="info.name" required maxlength="64" /></label>
          <label class="chat-field"><span>群公告</span><textarea v-model="info.announcement" rows="3" maxlength="1000" /></label>
          <div class="chat-actions"><button type="submit" class="chat-button is-primary" :disabled="busy || !info.name.trim()">保存群资料</button><button type="button" class="chat-button" :disabled="busy" @click="cancelEdit">取消</button></div>
        </form>
        <div v-else class="chat-info-row">
          <Megaphone class="chat-info-icon" aria-hidden="true" />
          <div class="chat-info-text"><p class="preserve-text">{{ group.data.value.announcement || '还没有群公告' }}</p><p class="chat-info-label">群公告</p></div>
          <button v-if="!group.data.value.dismissed" type="button" class="chat-icon-button is-small" aria-label="编辑群名称和公告" @click="editing = true"><Pencil aria-hidden="true" /></button>
        </div>
      </section>

      <section class="chat-info-section" aria-label="成员">
        <h3>成员（{{ members.length }}）</h3>
        <ul class="chat-member-list">
          <li v-if="!group.data.value.dismissed">
            <button type="button" class="chat-member chat-member-add" :aria-expanded="inviting" @click="inviting = !inviting"><span class="chat-avatar chat-avatar-md chat-avatar-soft" aria-hidden="true"><UserPlus /></span>拉实例入群</button>
            <form v-if="inviting" class="chat-inline-form" @submit.prevent="add">
              <label class="chat-field"><span>拉入实例</span><select v-model="adding" :disabled="busy || !addable.length"><option value="">{{ addable.length ? '选择实例' : '没有可拉入的实例' }}</option><option v-for="c in addable" :key="c.im_user_id" :value="c.instance_id!">{{ c.nickname }}</option></select></label>
              <button type="submit" class="chat-button is-primary" :disabled="busy || !adding">拉入</button>
            </form>
          </li>
          <li v-for="member in members" :key="member.im_user_id" class="chat-member">
            <ChatAvatar :name="member.nickname" size="md" :status="member.instance_id && member.online ? 'online' : null" />
            <span class="chat-contact-who"><span class="chat-contact-name">{{ member.nickname }}</span><span class="chat-contact-status" :class="{ 'is-online': member.instance_id && member.online }">{{ memberLine(member) }}</span></span>
            <button v-if="member.role !== 'owner' && !group.data.value.dismissed" type="button" class="chat-button is-quiet" :disabled="busy" :aria-label="`把 ${member.nickname} 移出群`" @click="remove(member)">移出</button>
          </li>
        </ul>
      </section>

      <section v-if="!group.data.value.dismissed" class="chat-info-section" aria-label="防刷屏">
        <h3>防刷屏</h3>
        <form class="chat-stack-form" @submit.prevent="savePolicy">
          <label class="chat-switch-row">
            <span><span>允许实例之间接力</span><span class="chat-info-label">关闭后实例 @ 其他实例的消息会被拒收</span></span>
            <input v-model="policy.relay_enabled" type="checkbox" class="chat-switch-input" />
          </label>
          <label class="chat-field is-row"><span>管理员不发言时，实例最多连续发言（条）</span><input v-model.number="policy.max_agent_streak" type="number" min="1" max="50" required /></label>
          <label class="chat-field is-row"><span>单个实例每分钟最多（条）</span><input v-model.number="policy.max_per_minute" type="number" min="1" max="60" required /></label>
          <button type="submit" class="chat-button" :disabled="busy">保存防刷屏设置</button>
        </form>
      </section>

      <p v-if="error" role="alert" class="chat-info-note is-error">{{ error }}</p>
      <p v-if="notice" role="status" class="chat-info-note">{{ notice }}</p>
      <div v-if="!group.data.value.dismissed" class="chat-info-section">
        <button type="button" class="chat-button is-danger" :disabled="busy" @click="dismiss">解散群</button>
      </div>
    </template>
  </div>
</template>
