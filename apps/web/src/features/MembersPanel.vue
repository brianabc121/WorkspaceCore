<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../stores/auth'
import { ApiError, dataOf, errorMessage, isAccessError, type Schema } from '../services/api'
import DataState from '../components/DataState.vue'
import ActionFeedback from '../components/ActionFeedback.vue'
import { formatTime } from './time'

// Workspace members (spec §9.2, T-0064). admin manages member / viewer; granting, revoking or removing admin
// is owner-only; the owner cannot be changed. The server enforces all of this — the page only hides choices.
type Member = Schema['MemberOut']
type Assignable = Schema['MemberUpdate']['role']
const props = defineProps<{ workspaceId: string }>()
const auth = useAuthStore()
const ROLE_LABELS: Record<Member['role'], string> = { owner: 'owner', admin: 'admin', member: 'member', viewer: 'viewer' }
const assignable = computed<Assignable[]>(() => auth.isOwner ? ['admin', 'member', 'viewer'] : ['member', 'viewer'])
const query = useQuery({
  queryKey: computed(() => ['members', auth.user?.id, props.workspaceId]),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/workspaces/{workspace_id}/members', { params: { path: { workspace_id: props.workspaceId } }, signal })),
  enabled: computed(() => auth.canManage && auth.workspaceId === props.workspaceId),
})
const busy = ref(false), error = ref(''), notice = ref('')
const form = reactive({ email: '', role: 'member' as Assignable, create: false, display_name: '', password: '' })
// Server messages for these codes already say what to do; other errors use the shared wording.
const OWN_MESSAGES = new Set(['MEMBER_EXISTS', 'USER_EXISTS', 'FORBIDDEN'])
function explain(cause: unknown) {
  if (cause instanceof ApiError && cause.code === 'USER_NOT_FOUND') return '没有这个邮箱的账号：勾选“同时创建账号”，填写显示名与初始密码。'
  if (cause instanceof ApiError && OWN_MESSAGES.has(cause.code) && cause.message) return cause.message
  return errorMessage(cause)
}
async function act(effect: () => Promise<unknown>, success: string) {
  if (busy.value) return false
  busy.value = true; error.value = ''; notice.value = ''
  try { await effect(); notice.value = success; await query.refetch(); return true }
  catch (cause) { error.value = explain(cause); return false }
  finally { busy.value = false }
}
const canEdit = (m: Member) => !m.you && m.role !== 'owner' && (auth.isOwner || m.role !== 'admin')
const path = (m: Member) => ({ workspace_id: props.workspaceId, user_id: m.user_id })
async function add() {
  const email = form.email.trim()
  if (!email) return
  if (form.create && form.password.length < 12) { error.value = '初始密码至少 12 个字符。'; return }
  const body: Schema['MemberCreate'] = { email, role: form.role, ...(form.create ? { display_name: form.display_name.trim(), password: form.password } : {}) }
  const ok = await act(async () => dataOf(await auth.api.POST('/api/v1/workspaces/{workspace_id}/members', { params: { path: { workspace_id: props.workspaceId } }, body })),
    form.create ? `已创建账号并加入：${email}（请把初始密码告诉对方，并提醒登录后修改）` : `已加入：${email}`)
  if (ok) Object.assign(form, { email: '', create: false, display_name: '', password: '' })
}
const changeRole = (m: Member, role: Assignable) => act(async () => dataOf(await auth.api.PATCH('/api/v1/workspaces/{workspace_id}/members/{user_id}', { params: { path: path(m) }, body: { role } })), `${m.display_name} 的角色已改为 ${role}`)
function remove(m: Member) {
  if (!window.confirm(`把 ${m.display_name}（${m.email}）移出工作空间？移出后对方立即失去访问权限。`)) return
  void act(() => auth.api.DELETE('/api/v1/workspaces/{workspace_id}/members/{user_id}', { params: { path: path(m) } }), `已移出：${m.email}`)
}
</script>

<template>
  <section class="panel form-panel" aria-label="成员">
    <div class="section-heading"><h2>成员</h2><button type="button" class="secondary-button" :disabled="query.isFetching.value" @click="query.refetch()">刷新成员</button></div>
    <p class="form-help">{{ auth.isOwner ? 'owner 可以授予或收回 admin。' : 'admin 可以管理 member 与 viewer；授予或收回 admin 需要 owner。' }}owner 不能被修改或移除。</p>
    <DataState :loading="query.isPending.value" :error="query.error.value" :empty="!query.data.value?.items.length" empty-text="暂无成员" :stale="!!query.data.value && !isAccessError(query.error.value)" @retry="query.refetch()" />
    <ul v-if="query.data.value && !isAccessError(query.error.value)" class="record-list">
      <li v-for="m in query.data.value.items" :key="m.user_id" :aria-label="`成员：${m.display_name}`">
        <div><strong>{{ m.display_name }}</strong><template v-if="m.you">（你）</template><p>{{ m.email }}</p><p>加入：{{ formatTime(m.joined_at) }}</p></div>
        <div class="form-actions">
          <label v-if="canEdit(m)" class="form-field"><span>角色</span>
            <select :value="m.role" :disabled="busy" @change="changeRole(m, ($event.target as HTMLSelectElement).value as Assignable)">
              <option v-for="r in assignable" :key="r" :value="r">{{ ROLE_LABELS[r] }}</option>
            </select>
          </label>
          <span v-else class="badge">{{ ROLE_LABELS[m.role] }}</span>
          <button v-if="canEdit(m)" type="button" class="secondary-button" :disabled="busy" @click="remove(m)">移出</button>
        </div>
      </li>
    </ul>
    <form class="im-group-form" aria-label="添加成员" @submit.prevent="add">
      <h3>添加成员</h3>
      <label class="form-field"><span>邮箱 *</span><input v-model="form.email" type="email" required maxlength="254" autocomplete="off" /></label>
      <label class="form-field"><span>角色</span><select v-model="form.role"><option v-for="r in assignable" :key="r" :value="r">{{ ROLE_LABELS[r] }}</option></select></label>
      <label class="check-field"><input v-model="form.create" type="checkbox" /> 同时创建账号（这个邮箱还没有账号时）</label>
      <template v-if="form.create">
        <label class="form-field"><span>显示名 *</span><input v-model="form.display_name" required maxlength="64" /></label>
        <label class="form-field"><span>初始密码 *（至少 12 个字符）</span><input v-model="form.password" type="password" required minlength="12" maxlength="128" autocomplete="new-password" /></label>
      </template>
      <ActionFeedback :error="error" :notice="notice" />
      <button type="submit" class="primary-button" :disabled="busy || !form.email.trim() || (form.create && !form.display_name.trim())">{{ busy ? '正在提交…' : '添加成员' }}</button>
    </form>
  </section>
</template>
