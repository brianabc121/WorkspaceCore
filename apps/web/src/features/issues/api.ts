import { computed, type Ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'

// 项目里的 Issues（T-0124，docs/v2/AGENT_MESSAGING_PLAN.md 8.8；接口见 T-0123）。
// 查询键都以 'issues' 开头：SSE 的 issues 主题会刷新它们（features/events/useWorkspaceEvents.ts）。
export type Issue = Schema['ProjectIssueOut']
export type IssueDetail = Schema['IssueDetail']
export type IssueKind = Issue['kind']
export type CloseReason = NonNullable<Issue['close_reason']>
export type Actor = Schema['Actor']
export type IssueComment = Schema['CommentOut']
export type IssueEvent = Schema['IssueEventOut']
export type IssueFilters = { state: 'open' | 'closed'; kind: IssueKind | ''; label: string; assignedToMe: boolean; mentionedMe: boolean; q: string }

export const KIND_LABELS: Record<IssueKind, string> = {
  question: '提问', proposal: '提议', bug: '缺陷', contract: '接口约定', handoff: '交接', review: '评审请求', blocker: '阻塞', other: '其他',
}
export const KINDS = Object.keys(KIND_LABELS) as IssueKind[]
export const CLOSE_REASONS: Record<CloseReason, string> = { resolved: '已解决', wontfix: '不做', duplicate: '重复' }

function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  return { auth, user, header: { 'X-Workspace-Id': scope }, active: () => user === auth.user?.id && scope === auth.workspaceId }
}
export const issueKey = (user: string | undefined, scope: string, ...parts: unknown[]) => ['issues', user, scope, ...parts]

export function useIssueTemplates(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: issueKey(c.user, scope, 'templates'), enabled: computed(c.active), staleTime: Infinity,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/issue-templates', { params: { header: c.header }, signal })) })
}

export function useProjectIssues(scope: string, projectId: Ref<string>, filters: Ref<IssueFilters>) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => issueKey(c.user, scope, 'project', projectId.value, { ...filters.value })), enabled: computed(c.active),
    queryFn: async ({ signal }) => {
      const f = filters.value
      const query = {
        state: f.state, ...(f.kind ? { kind: f.kind } : {}), ...(f.label.trim() ? { label: f.label.trim() } : {}),
        ...(f.assignedToMe ? { assigned_to_me: true } : {}), ...(f.mentionedMe ? { mentioned_me: true } : {}), ...(f.q.trim() ? { q: f.q.trim() } : {}),
      }
      return dataOf(await c.auth.api.GET('/api/v1/projects/{project_id}/issues', { params: { header: c.header, path: { project_id: projectId.value }, query }, signal }))
    },
  })
}

export function useIssue(scope: string, id: Ref<string | null>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => issueKey(c.user, scope, 'one', id.value)), enabled: computed(() => c.active() && !!id.value),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/issues/{issue_id}', { params: { header: c.header, path: { issue_id: id.value! } }, signal })) })
}

export function useIssueByNumber(scope: string, projectId: Ref<string>, number: Ref<number>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => issueKey(c.user, scope, 'number', projectId.value, number.value)), enabled: computed(() => c.active() && number.value > 0),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/projects/{project_id}/issues/{number}', { params: { header: c.header, path: { project_id: projectId.value, number: number.value } }, signal })) })
}

export function useTaskIssues(scope: string, taskId: Ref<string>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => issueKey(c.user, scope, 'task', taskId.value)), enabled: computed(c.active),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/tasks/{task_id}/issues', { params: { header: c.header, path: { task_id: taskId.value } }, signal })) })
}

// 写操作：成功后刷新 Issues 与搜索、README（README 的“沟通”“讨论”会随之更新）
export function useIssueActions(scope: string) {
  const c = context(scope), cache = useQueryClient()
  const refresh = () => cache.invalidateQueries({ predicate: (q) => ['issues', 'search', 'kb', 'notifications'].includes(String(q.queryKey[0])) && q.queryKey[2] === scope })
  const api = c.auth.api, header = c.header
  return {
    refresh,
    async create(projectId: string, body: Schema['IssueCreate']) {
      const created = dataOf(await api.POST('/api/v1/projects/{project_id}/issues', { params: { header, path: { project_id: projectId } }, body }))
      await refresh(); return created
    },
    async update(issue: Pick<IssueDetail, 'id' | 'version'>, body: Omit<Schema['IssueUpdate'], 'version'>) {
      const saved = dataOf(await api.PATCH('/api/v1/issues/{issue_id}', { params: { header, path: { issue_id: issue.id } }, body: { version: issue.version, ...body } }))
      await refresh(); return saved
    },
    async comment(issueId: string, body: string, replyTo: string | null = null) {
      const saved = dataOf(await api.POST('/api/v1/issues/{issue_id}/comments', { params: { header, path: { issue_id: issueId } }, body: { body, reply_to: replyTo } }))
      await refresh(); return saved
    },
    async hide(issueId: string, commentId: string) {
      dataOf(await api.POST('/api/v1/issues/{issue_id}/comments/{comment_id}/hide', { params: { header, path: { issue_id: issueId, comment_id: commentId } } }))
      await refresh()
    },
    async remove(issueId: string) {
      await api.DELETE('/api/v1/issues/{issue_id}', { params: { header, path: { issue_id: issueId } } }).then(dataOf)
      await refresh()
    },
    async read(issueId: string) {
      await api.POST('/api/v1/issues/{issue_id}/read', { params: { header, path: { issue_id: issueId } } })
      await cache.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'issues' && q.queryKey[2] === scope && q.queryKey[3] !== 'one' })
    },
    async notices(projectId: string, enabled: boolean) {
      return dataOf(await api.PUT('/api/v1/projects/{project_id}/issue-notices', { params: { header, path: { project_id: projectId } }, body: { enabled } }))
    },
  }
}

export const actorName = (actor: Actor) => (actor.kind === 'instance' ? `智能体「${actor.name}」` : actor.name)
