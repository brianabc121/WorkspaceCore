import { computed, type Ref } from 'vue'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, errorMessage, type Schema } from '../../services/api'

// 知识库（T-0099，docs/v2/KNOWLEDGE_PLAN.md；接口见 docs/api-contract.md“知识库（T-0098）”）。
// 查询键都以 'kb' 开头：SSE 的 knowledge 主题会刷新它们（features/events/useWorkspaceEvents.ts）。
export type Space = Schema['SpaceOut']
export type SpaceKind = Space['kind']
export type Note = Schema['NoteOut']
export type NoteSummary = Schema['NoteSummary']
export type NoteType = Note['type']
export type Author = Schema['Author']
export type GraphOut = Schema['GraphOut']
export type GraphNode = Schema['GraphNode']
export type Change = Schema['ChangeOut']
export type Revision = Schema['RevisionOut']
export type AgentKnowledge = Schema['AgentKnowledgeOut']
export type SpaceActivity = Schema['SpaceActivity']
export type ColorBy = 'type' | 'space' | 'author'

export const SPACE_KIND_LABELS: Record<SpaceKind, string> = { public: '公共', project: '项目', agent: '智能体' }
export const NOTE_TYPE_LABELS: Record<NoteType, string> = {
  overview: '概述', brief: '项目简报', decision: '决策', rule: '规范', process: '流程', runbook: '操作手册', lesson: '经验',
  review: '复盘', preference: '偏好', glossary: '术语', record: '记录', plan: '计划', risk: '风险', design: '设计', template: '模板',
  correction: '收到的纠正',
}
// 图谱按类型着色（T-0102，docs/v2/TIMELINE_PLAN.md 3.1）：16 种类型按用途分 6 组，一组一个颜色。
// 超过 8 种颜色人眼分不清，所以颜色只区分组；具体类型看图例、悬停提示与列表视图。
export type TypeGroup = 'rule' | 'decision' | 'lesson' | 'project' | 'knowledge' | 'preference'
export const TYPE_GROUPS: readonly { key: TypeGroup; label: string; types: readonly NoteType[] }[] = [
  { key: 'rule', label: '规范与流程', types: ['rule', 'process', 'runbook', 'template'] },
  { key: 'decision', label: '决策与计划', types: ['decision', 'plan', 'design'] },
  { key: 'lesson', label: '经验与复盘', types: ['lesson', 'review', 'correction'] },
  { key: 'project', label: '项目', types: ['brief', 'risk'] },
  { key: 'knowledge', label: '知识', types: ['overview', 'glossary', 'record'] },
  { key: 'preference', label: '偏好', types: ['preference'] },
]
const GROUP_OF = new Map(TYPE_GROUPS.flatMap(g => g.types.map(t => [t, g.key] as const)))
export const typeGroupOf = (type: NoteType | null | undefined): TypeGroup => (type && GROUP_OF.get(type)) || 'knowledge'
// 人能新建的类型：项目简报只在项目库里建一篇，《收到的纠正》由工作台维护
export const EDITABLE_TYPES = (Object.keys(NOTE_TYPE_LABELS) as NoteType[]).filter(t => t !== 'correction')
export const CHANGE_LABELS: Record<NonNullable<Change['change']>, string> = { create: '新建', update: '修改', delete: '删除', restore: '恢复', revert: '撤销', rename: '改名' }
export const GUARD_LABELS: Record<NonNullable<Change['guard']>, string> = { secret_blocked: '拦下写入', delete_paused: '暂停删除', rate_limited: '写入限流', writes_disabled: '写入已关闭' }

function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  return { auth, user, header: { 'X-Workspace-Id': scope }, active: () => user === auth.user?.id && scope === auth.workspaceId }
}
export const kbKey = (user: string | undefined, scope: string, ...parts: unknown[]) => ['kb', user, scope, ...parts]

export function useSpaces(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: kbKey(c.user, scope, 'spaces'), enabled: computed(c.active),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/knowledge/spaces', { params: { header: c.header }, signal })) })
}

export type NoteFilters = { space_id?: string; folder?: string; type?: NoteType; tag?: string; author?: 'user' | 'agent'; q?: string }
export function useNotes(scope: string, filters: Ref<NoteFilters>, enabled: Ref<boolean> = computed(() => true)) {
  const c = context(scope)
  return useInfiniteQuery({
    queryKey: computed(() => kbKey(c.user, scope, 'notes', filters.value)), enabled: computed(() => c.active() && enabled.value),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      const query = Object.fromEntries(Object.entries({ ...filters.value, limit: 100, cursor: pageParam ?? undefined }).filter(([, v]) => v !== undefined && v !== ''))
      return dataOf(await c.auth.api.GET('/api/v1/knowledge/notes', { params: { header: c.header, query }, signal }))
    },
    getNextPageParam: p => p.next_cursor ?? undefined,
  })
}

export function useNote(scope: string, id: Ref<string | null>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => kbKey(c.user, scope, 'note', id.value)), enabled: computed(() => c.active() && !!id.value),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/knowledge/notes/{note_id}', { params: { header: c.header, path: { note_id: id.value! } }, signal })) })
}

export function useRevisions(scope: string, id: Ref<string | null>, enabled: Ref<boolean>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => kbKey(c.user, scope, 'revisions', id.value)), enabled: computed(() => c.active() && !!id.value && enabled.value),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/knowledge/notes/{note_id}/revisions', { params: { header: c.header, path: { note_id: id.value! } }, signal })) })
}

export function useRevision(scope: string, id: Ref<string | null>, version: Ref<number | null>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => kbKey(c.user, scope, 'revision', id.value, version.value)), enabled: computed(() => c.active() && !!id.value && !!version.value),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/knowledge/notes/{note_id}/revisions/{version}', { params: { header: c.header, path: { note_id: id.value!, version: version.value! } }, signal })) })
}

export function useGraph(scope: string, options: Ref<{ spaceIds: string[]; resources: boolean }>) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => kbKey(c.user, scope, 'graph', options.value)), enabled: computed(c.active),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/knowledge/graph', {
      params: { header: c.header, query: { resources: options.value.resources, ...(options.value.spaceIds.length ? { space_id: options.value.spaceIds } : {}) } }, signal })),
  })
}

export function useLocalGraph(scope: string, id: Ref<string | null>, depth: Ref<number>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => kbKey(c.user, scope, 'local-graph', id.value, depth.value)), enabled: computed(() => c.active() && !!id.value),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/knowledge/notes/{note_id}/graph', { params: { header: c.header, path: { note_id: id.value! }, query: { depth: depth.value } }, signal })) })
}

export type ChangeFilters = { space_id?: string; instance_id?: string; kind?: 'create' | 'update' | 'delete' | 'restore' | 'revert' | 'rename' | 'guard' }
export function useChanges(scope: string, filters: Ref<ChangeFilters>) {
  const c = context(scope)
  return useInfiniteQuery({
    queryKey: computed(() => kbKey(c.user, scope, 'changes', filters.value)), enabled: computed(c.active), initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      const query = Object.fromEntries(Object.entries({ ...filters.value, limit: 30, cursor: pageParam ?? undefined }).filter(([, v]) => v !== undefined && v !== ''))
      return dataOf(await c.auth.api.GET('/api/v1/knowledge/changes', { params: { header: c.header, query }, signal }))
    },
    getNextPageParam: p => p.next_cursor ?? undefined,
  })
}

export function useTrash(scope: string, enabled: Ref<boolean>) {
  const c = context(scope)
  return useInfiniteQuery({
    queryKey: kbKey(c.user, scope, 'trash'), enabled: computed(() => c.active() && enabled.value), initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => dataOf(await c.auth.api.GET('/api/v1/knowledge/trash', { params: { header: c.header, query: { limit: 50, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
    getNextPageParam: p => p.next_cursor ?? undefined,
  })
}

export function useAgentKnowledge(scope: string, instanceId: Ref<string | null>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => kbKey(c.user, scope, 'agent', instanceId.value)), enabled: computed(() => c.active() && !!instanceId.value),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/knowledge/agents/{instance_id}', { params: { header: c.header, path: { instance_id: instanceId.value! } }, signal })) })
}

// 近 8 周人 / 智能体的修改次数（T-0100，知识库主页的趋势图）
export function useSpaceActivity(scope: string, spaceId: Ref<string | null>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => kbKey(c.user, scope, 'activity', spaceId.value)), enabled: computed(() => c.active() && !!spaceId.value),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/knowledge/spaces/{space_id}/activity', { params: { header: c.header, path: { space_id: spaceId.value! } }, signal })) })
}

// 写操作：成功后刷新所有 kb 查询（全局搜索也会变）
export function useKb(scope: string) {
  const c = context(scope), cache = useQueryClient()
  const path = <T extends string>(note_id: T) => ({ note_id })
  const refresh = () => cache.invalidateQueries({ predicate: q => (q.queryKey[0] === 'kb' && q.queryKey[1] === c.user && q.queryKey[2] === scope) || q.queryKey[0] === 'search' })
  const after = async <T>(work: Promise<T>) => { const value = await work; await refresh(); return value }
  return {
    refresh,
    suggest: async (q: string, signal?: AbortSignal) => dataOf(await c.auth.api.GET('/api/v1/knowledge/notes/suggest', { params: { header: c.header, query: { q, limit: 8 } }, signal })).items,
    create: (body: Schema['NoteCreate']) => after((async () => dataOf(await c.auth.api.POST('/api/v1/knowledge/notes', { params: { header: c.header }, body })))()),
    update: (id: string, body: Schema['NoteUpdate']) => after((async () => dataOf(await c.auth.api.PATCH('/api/v1/knowledge/notes/{note_id}', { params: { header: c.header, path: path(id) }, body })))()),
    remove: (id: string, version: number, reason = '') => after((async () => { await c.auth.api.DELETE('/api/v1/knowledge/notes/{note_id}', { params: { header: c.header, path: path(id), query: { version, reason } } }) })()),
    restore: (id: string) => after((async () => dataOf(await c.auth.api.POST('/api/v1/knowledge/notes/{note_id}/restore', { params: { header: c.header, path: path(id) } })))()),
    revert: (id: string, version: number, to_version: number, reason: string) => after((async () => dataOf(await c.auth.api.POST('/api/v1/knowledge/notes/{note_id}/revert', { params: { header: c.header, path: path(id) }, body: { version, to_version, reason } })))()),
    purge: (id: string) => after((async () => { await c.auth.api.DELETE('/api/v1/knowledge/trash/{note_id}', { params: { header: c.header, path: path(id) } }) })()),
    updateSpace: (id: string, body: Schema['SpaceUpdate']) => after((async () => dataOf(await c.auth.api.PATCH('/api/v1/knowledge/spaces/{space_id}', { params: { header: c.header, path: { space_id: id } }, body })))()),
    putAgent: (spaceId: string, instanceId: string, access: 'read_write' | 'read') => after((async () => dataOf(await c.auth.api.PUT('/api/v1/knowledge/spaces/{space_id}/agents/{instance_id}', { params: { header: c.header, path: { space_id: spaceId, instance_id: instanceId } }, body: { access } })))()),
    removeAgent: (spaceId: string, instanceId: string) => after((async () => { await c.auth.api.DELETE('/api/v1/knowledge/spaces/{space_id}/agents/{instance_id}', { params: { header: c.header, path: { space_id: spaceId, instance_id: instanceId } } }) })()),
  }
}

export function authorLabel(author: Author | null | undefined) {
  if (!author) return '未知'
  return author.kind === 'agent' ? `${author.name}（智能体）` : author.kind === 'system' ? '工作台' : author.name
}
export const initial = (name: string) => (name.trim()[0] ?? '?').toUpperCase()

// 知识库自己的错误码：服务端的说明是工作台写的中文，直接显示（其余的按通用说明）
const KB_CODES = new Set(['TITLE_TAKEN', 'SECRET_DETECTED', 'NOTE_TOO_LARGE', 'SPACE_FULL', 'NOTE_DELETED', 'NOT_IN_TRASH', 'REVISION_NOT_FOUND', 'MOVE_NOT_ALLOWED', 'SYSTEM_NOTE', 'BRIEF_HUMAN_ONLY', 'READ_ONLY', 'WRITES_DISABLED', 'DELETES_PAUSED'])
export function kbError(error: unknown) {
  if (error instanceof ApiError && KB_CODES.has(error.code) && error.message) return error.message
  return errorMessage(error)
}
