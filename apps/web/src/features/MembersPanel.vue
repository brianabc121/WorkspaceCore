<script setup lang="ts">
import { computed, nextTick, reactive, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../stores/auth'
import { ApiError, dataOf, errorMessage, isAccessError, type Schema } from '../services/api'
import DataState from '../components/DataState.vue'
import ActionFeedback from '../components/ActionFeedback.vue'
import { formatTime } from './time'
import { useCreateIntent } from '../composables/useCreateIntent'
import { RefreshCw, UserPlus, Info } from '@lucide/vue'
import { ROLE_NAMES, initialOf } from '../components/management/roles'

// Workspace members (spec §9.2, T-0064). admin manages member / viewer; granting, revoking or removing admin
// is owner-only; the owner cannot be changed. The server enforces all of this — the page only hides choices.
type Member = Schema['MemberOut']
type Assignable = Schema['MemberUpdate']['role']
const props = defineProps<{ workspaceId: string }>()
const auth = useAuthStore()
const ROLE_LABELS: Record<Member['role'], string> = { owner: `${ROLE_NAMES.owner} owner`, admin: `${ROLE_NAMES.admin} admin`, member: `${ROLE_NAMES.member} member`, viewer: `${ROLE_NAMES.viewer} viewer` }
const assignable = computed<Assignable[]>(() => auth.isOwner ? ['admin', 'member', 'viewer'] : ['member', 'viewer'])
const query = useQuery({
  queryKey: computed(() => ['members', auth.user?.id, props.workspaceId]),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/workspaces/{workspace_id}/members', { params: { path: { workspace_id: props.workspaceId } }, signal })),
  enabled: computed(() => auth.canManage && auth.workspaceId === props.workspaceId),
})
const busy = ref(false), error = ref(''), notice = ref('')
// 顶栏“创建 → 添加成员”（规范 §8.1）：把焦点移到添加成员表单的邮箱框
const emailInput = ref<HTMLInputElement | null>(null)
const focusAdd = () => { void nextTick(() => { emailInput.value?.focus(); emailInput.value?.scrollIntoView?.({ block: 'center' }) }) }
useCreateIntent(() => true, focusAdd)
const count = computed(() => query.data.value?.items.length ?? null)
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
  <section class="ws-panel mg-section st-members" aria-label="成员">
    <div class="mg-section-head">
      <div class="mg-section-title">
        <h2>成员与角色<template v-if="count !== null"> · {{ count }}</template></h2>
        <p class="form-help st-rule"><Info aria-hidden="true" />{{ auth.isOwner ? '所有者可以授予或收回管理员；管理员管理成员与访客。' : '管理员管理成员与访客；授予或收回管理员需要所有者。' }}所有者不能被修改或移出。</p>
      </div>
      <div class="mg-section-actions">
        <button type="button" class="secondary-button" :disabled="query.isFetching.value" @click="query.refetch()"><RefreshCw aria-hidden="true" />刷新</button>
        <button type="button" class="primary-button" @click="focusAdd"><UserPlus aria-hidden="true" />添加成员</button>
      </div>
    </div>
    <DataState :loading="query.isPending.value" :error="query.error.value" :empty="!query.data.value?.items.length" empty-text="暂无成员" :stale="!!query.data.value && !isAccessError(query.error.value)" @retry="query.refetch()" />
    <table v-if="query.data.value?.items.length && !isAccessError(query.error.value)" class="ws-table asv-table st-member-table">
      <caption class="sr-only">工作空间成员</caption>
      <thead><tr><th scope="col">成员</th><th scope="col">邮箱</th><th scope="col">角色</th><th scope="col">操作</th></tr></thead>
      <tbody>
        <tr v-for="m in query.data.value.items" :key="m.user_id" :aria-label="`成员：${m.display_name}`">
          <th scope="row"><span class="st-member"><span class="mg-avatar is-small" aria-hidden="true">{{ initialOf(m.display_name, m.email) }}</span><span>{{ m.display_name }}<span v-if="m.you">（你）</span><span class="asv-sub">加入：{{ formatTime(m.joined_at) }}</span></span></span></th>
          <td data-label="邮箱" class="st-email">{{ m.email }}</td>
          <td data-label="角色">
            <select v-if="canEdit(m)" :value="m.role" :disabled="busy" aria-label="角色" @change="changeRole(m, ($event.target as HTMLSelectElement).value as Assignable)">
              <option v-for="r in assignable" :key="r" :value="r">{{ ROLE_LABELS[r] }}</option>
            </select>
            <span v-else class="mg-role" :class="`is-${m.role}`">{{ ROLE_NAMES[m.role] }} <small>{{ m.role }}</small></span>
          </td>
          <td data-label="操作" class="asv-link-cell">
            <button v-if="canEdit(m)" type="button" class="asv-link" :disabled="busy" @click="remove(m)">移出</button>
            <span v-else class="asv-sub">不可修改</span>
          </td>
        </tr>
      </tbody>
    </table>
    <form class="st-add" aria-label="添加成员" @submit.prevent="add">
      <h3>添加成员</h3>
      <div class="st-add-row mg-stack-narrow">
        <label class="form-field"><span>邮箱 *</span><input ref="emailInput" v-model="form.email" type="email" required maxlength="254" autocomplete="off" placeholder="name@example.com" /></label>
        <label class="form-field"><span>角色</span><select v-model="form.role"><option v-for="r in assignable" :key="r" :value="r">{{ ROLE_LABELS[r] }}</option></select></label>
        <label class="check-field"><input v-model="form.create" type="checkbox" /> 同时创建账号</label>
        <button type="submit" class="primary-button" :disabled="busy || !form.email.trim() || (form.create && !form.display_name.trim())">{{ busy ? '正在提交…' : '添加成员' }}</button>
      </div>
      <p class="form-help">添加的是已有账号；这个邮箱还没有账号时，选上“同时创建账号”再填写显示名与初始密码（不会发送邀请邮件）。</p>
      <div v-if="form.create" class="st-add-row mg-stack-narrow">
        <label class="form-field"><span>显示名 *</span><input v-model="form.display_name" required maxlength="64" /></label>
        <label class="form-field"><span>初始密码 *（至少 12 个字符）</span><input v-model="form.password" type="password" required minlength="12" maxlength="128" autocomplete="new-password" /></label>
      </div>
      <ActionFeedback :error="error" :notice="notice" />
    </form>
  </section>
</template>
