<script setup lang="ts">
// 项目页的 Issues 标签（T-0124，方案 8.8）：按状态、类型、标签、指派给我、提到我筛选，可以搜索；新建先选类型。
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { errorMessage } from '../../services/api'
import DataState from '../../components/DataState.vue'
import { formatTime } from '../time'
import IssueForm from './IssueForm.vue'
import { KIND_LABELS, KINDS, actorName, useIssueActions, useProjectIssues, type IssueDetail, type IssueFilters } from './api'

const props = defineProps<{ projectId: string; writable: boolean; admin: boolean; notices?: boolean }>()
const auth = useAuthStore(), scope = auth.workspaceId!, router = useRouter()
const filters = ref<IssueFilters>({ state: 'open', kind: '', label: '', assignedToMe: false, mentionedMe: false, q: '' })
const list = useProjectIssues(scope, computed(() => props.projectId), filters)
const items = computed(() => list.data.value?.items ?? [])
const creating = ref(false)
function created(issue: IssueDetail) { creating.value = false; void router.push(`/issues/${issue.id}`) }
// 项目群里的“#12 新开 / 已关闭”提示（管理员，默认关闭）
const actions = useIssueActions(scope), noticeOn = ref(!!props.notices), noticeProblem = ref('')
async function toggleNotices() {
  noticeProblem.value = ''
  try { noticeOn.value = (await actions.notices(props.projectId, !noticeOn.value)).enabled } catch (cause) { noticeProblem.value = errorMessage(cause) }
}
</script>

<template>
  <section class="issues" aria-label="Issues">
    <div class="issues-bar">
      <div class="kb-segment" role="group" aria-label="Issue 状态">
        <button type="button" class="secondary-button" :aria-pressed="filters.state === 'open'" @click="filters.state = 'open'">未关闭 {{ list.data.value?.open_count ?? '' }}</button>
        <button type="button" class="secondary-button" :aria-pressed="filters.state === 'closed'" @click="filters.state = 'closed'">已关闭 {{ list.data.value?.closed_count ?? '' }}</button>
      </div>
      <button v-if="writable && !creating" type="button" class="primary-button" @click="creating = true">新建 Issue</button>
    </div>
    <IssueForm v-if="creating" :project-id="projectId" @created="created" @cancel="creating = false" />
    <form class="issues-filters" role="search" aria-label="筛选 Issue" @submit.prevent>
      <label class="form-field">类型<select v-model="filters.kind"><option value="">全部类型</option><option v-for="k in KINDS" :key="k" :value="k">{{ KIND_LABELS[k] }}</option></select></label>
      <label class="form-field">标签<input v-model.lazy="filters.label" maxlength="30"></label>
      <label class="form-field">关键词<input v-model.lazy="filters.q" type="search" maxlength="100"></label>
      <label class="chip"><input v-model="filters.assignedToMe" type="checkbox"> 指派给我</label>
      <label class="chip"><input v-model="filters.mentionedMe" type="checkbox"> 提到我</label>
    </form>
    <DataState :loading="list.isPending.value" :error="list.error.value" :empty="!items.length" empty-text="没有符合条件的 Issue。" @retry="list.refetch()" />
    <ul v-if="items.length" class="issue-rows">
      <li v-for="issue in items" :key="issue.id" :class="{ unread: issue.unread }">
        <RouterLink :to="`/issues/${issue.id}`" class="issue-row-title">
          <span class="issue-number">#{{ issue.number }}</span> {{ issue.title }}
        </RouterLink>
        <span class="badge">{{ KIND_LABELS[issue.kind] }}</span>
        <span v-for="label in issue.labels" :key="label" class="chip">{{ label }}</span>
        <span v-if="issue.unread" class="badge" aria-label="有未读动态">新</span>
        <span v-if="issue.agent_paused" class="badge">智能体暂停评论</span>
        <span v-if="issue.locked" class="badge">已锁定</span>
        <small class="form-help">
          {{ actorName(issue.author) }} 开的 · {{ issue.comment_count }} 条评论 · {{ formatTime(issue.last_activity_at) }}
          <template v-if="issue.assignees.length"> · 指派给 {{ issue.assignees.map(actorName).join('、') }}</template>
        </small>
      </li>
    </ul>
    <p v-if="admin" class="form-help">
      <label class="chip"><input type="checkbox" :checked="noticeOn" @change="toggleNotices"> 新开、关闭时在项目群里提示一行</label>
      <span v-if="noticeProblem" role="alert">{{ noticeProblem }}</span>
    </p>
  </section>
</template>

<style scoped>
.issues { display: flex; flex-direction: column; gap: 12px; }
.issues-bar { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; }
.issues-filters { display: flex; flex-wrap: wrap; align-items: end; gap: 8px; }
.issue-rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.issue-rows li { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 8px 10px; border: 1px solid var(--border, #ddd); border-radius: 8px; }
.issue-rows li.unread .issue-row-title { font-weight: 600; }
.issue-rows small { flex-basis: 100%; }
.issue-number { color: var(--muted, #666); }
</style>
