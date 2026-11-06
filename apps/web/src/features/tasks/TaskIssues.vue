<script setup lang="ts">
// 任务详情抽屉的“讨论”（T-0124，方案 8.7）：这个任务关联的 Issue；在这里新建的自动关联这个任务。
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import DataState from '../../components/DataState.vue'
import IssueForm from '../issues/IssueForm.vue'
import { CLOSE_REASONS, KIND_LABELS, useTaskIssues, type IssueDetail } from '../issues/api'
const props = defineProps<{ taskId: string; projectId: string | null; writable: boolean }>()
const auth = useAuthStore(), router = useRouter()
const list = useTaskIssues(auth.workspaceId!, computed(() => props.taskId))
const items = computed(() => list.data.value?.items ?? [])
const creating = ref(false)
function created(issue: IssueDetail) { creating.value = false; void router.push(`/issues/${issue.id}`) }
</script>
<template>
  <section class="task-issues" aria-label="讨论">
    <DataState :loading="list.isPending.value" :error="list.error.value" :empty="!items.length" empty-text="还没有关联的 Issue。" @retry="list.refetch()" />
    <ul v-if="items.length" class="task-issue-rows">
      <li v-for="issue in items" :key="issue.id">
        <RouterLink :to="`/issues/${issue.id}`">#{{ issue.number }} {{ issue.title }}</RouterLink>
        <span class="badge">{{ KIND_LABELS[issue.kind] }}</span>
        <span class="badge">{{ issue.state === 'open' ? '未关闭' : `已关闭（${CLOSE_REASONS[issue.close_reason!]}）` }}</span>
        <span v-if="issue.unread" class="badge" aria-label="有未读动态">新</span>
      </li>
    </ul>
    <p v-if="items.some((i) => i.kind === 'blocker' && i.state === 'open')" class="form-help">有未关闭的“阻塞”Issue：智能体完成不了这个任务，人照常可以完成。</p>
    <template v-if="writable && projectId">
      <IssueForm v-if="creating" :project-id="projectId" :task-ids="[taskId]" @created="created" @cancel="creating = false" />
      <button v-else type="button" class="secondary-button" @click="creating = true">新建 Issue</button>
    </template>
  </section>
</template>
<style scoped>
.task-issues { display: flex; flex-direction: column; gap: 10px; }
.task-issue-rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.task-issue-rows li { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
</style>
