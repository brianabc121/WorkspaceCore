<script setup lang="ts">
// 项目页（T-0122，方案 9.7、D18）：打开项目先看 README（导览），再去 Issues、任务、时间轴、知识库。
// Issues 标签（T-0124，方案 8.8）：地址里带 ?tab=issues。
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'
import { useProjectReadme } from '../kb/api'
import ReadmeView from '../kb/ReadmeView.vue'
import DataState from '../../components/DataState.vue'
import IssueList from '../issues/IssueList.vue'

const props = defineProps<{ projectId: string }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const project = useQuery({
  queryKey: computed(() => ['projects', user, scope, 'one', props.projectId]),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/projects/{project_id}', { params: { header: { 'X-Workspace-Id': scope }, path: { project_id: props.projectId } }, signal })),
})
const readme = useProjectReadme(scope, computed(() => props.projectId))
const writable = computed(() => !!auth.currentWorkspace && auth.currentWorkspace.role !== 'viewer')
const admin = computed(() => ['owner', 'admin'].includes(auth.currentWorkspace?.role ?? ''))
const route = useRoute(), router = useRouter()
const tab = computed(() => (route.query.tab === 'issues' ? 'issues' : 'readme'))
const show = (next: 'readme' | 'issues') => router.replace({ query: { ...route.query, tab: next === 'readme' ? undefined : next } })
</script>

<template>
  <DataState :loading="project.isPending.value" :error="project.error.value" :empty="false" empty-text="" @retry="project.refetch()" />
  <template v-if="project.data.value">
    <div class="page-heading">
      <h1>{{ project.data.value.title }} <span v-if="project.data.value.archived" class="badge">已归档</span></h1>
      <p v-if="project.data.value.description" class="note-text">{{ project.data.value.description }}</p>
    </div>
    <nav class="kb-segment project-tabs" role="group" aria-label="项目">
      <button type="button" class="secondary-button" :aria-pressed="tab === 'readme'" @click="show('readme')">README</button>
      <button type="button" class="secondary-button" :aria-pressed="tab === 'issues'" @click="show('issues')">Issues</button>
      <RouterLink class="secondary-button" :to="{ path: '/tasks', query: { project_id: projectId } }">任务</RouterLink>
      <RouterLink class="secondary-button" :to="{ path: '/tasks/timeline', query: { project: projectId } }">时间轴</RouterLink>
      <RouterLink v-if="readme.data.value" class="secondary-button" :to="`/knowledge/spaces/${readme.data.value.note.space_id}`">知识库</RouterLink>
      <RouterLink class="secondary-button" to="/projects">全部项目</RouterLink>
    </nav>
    <section v-if="tab === 'readme'" class="panel form-panel">
      <DataState :loading="readme.isPending.value" :error="readme.error.value" :empty="false" empty-text="" @retry="readme.refetch()" />
      <ReadmeView v-if="readme.data.value" :readme="readme.data.value" :writable="writable" />
    </section>
    <IssueList v-else :project-id="projectId" :writable="writable && !project.data.value.archived" :admin="admin" :notices="project.data.value.issue_notices" />
  </template>
</template>

<style scoped>
.project-tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
</style>
