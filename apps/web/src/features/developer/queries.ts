import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { useInfiniteQuery, useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
export type GitHubKind = 'repos' | 'issues' | 'pulls' | 'runs'
export type GitHubRecord = Schema['RepoOut'] | Schema['IssueOut'] | Schema['PullOut'] | Schema['RunOut']
export type GitHubPage = { items: GitHubRecord[]; sync: Schema['SyncState']; next_cursor?: string | null; total_estimate?: number | null }
export type GitHubFilters = { repo_id?: string; state?: 'open' | 'closed' | 'all'; q?: string; status?: 'queued' | 'in_progress' | 'completed'; conclusion?: 'failure' | 'success'; limit?: number }
export function useGitHubList(kind: MaybeRefOrGetter<GitHubKind>, scope: string, filters: MaybeRefOrGetter<GitHubFilters> = {}, area = 'page', enabled: MaybeRefOrGetter<boolean> = true) {
  const auth = useAuthStore(), user = auth.user?.id, header = { 'X-Workspace-Id': scope }
  return useInfiniteQuery({ queryKey: computed(() => ['developer', user, scope, area, toValue(kind), toValue(filters)]), enabled: computed(() => toValue(enabled)), initialPageParam: null as string | null, queryFn: async ({ pageParam, signal }): Promise<GitHubPage> => {
    const f = toValue(filters), k = toValue(kind), common = { limit: f.limit ?? 30, ...(pageParam ? { cursor: pageParam } : {}) }
    if (k === 'repos') return dataOf(await auth.api.GET('/api/v1/developer/repos', { params: { header, query: common }, signal }))
    if (k === 'runs') return dataOf(await auth.api.GET('/api/v1/developer/runs', { params: { header, query: { ...common, repo_id: f.repo_id, status: f.status, conclusion: f.conclusion } }, signal }))
    const query = { ...common, repo_id: f.repo_id, state: f.state ?? 'open', q: f.q }
    return k === 'issues' ? dataOf(await auth.api.GET('/api/v1/developer/issues', { params: { header, query }, signal })) : dataOf(await auth.api.GET('/api/v1/developer/pulls', { params: { header, query }, signal }))
  }, getNextPageParam: (page) => page.next_cursor ?? undefined })
}
// 同步状态的优先级：失权 > 未配置 > 限流 > 不可用 > 过期 > 正常（多页、多个查询合在一起时取最严重的）
export function worstSync(states: (Schema['SyncState'] | undefined)[]): Schema['SyncState'] | undefined {
  const list = states.filter((s): s is Schema['SyncState'] => !!s)
  return list.find((s) => s.status === 'unauthorized') ?? list.find((s) => s.status === 'not_configured') ?? list.find((s) => s.reason === 'rate_limited') ?? list.find((s) => s.status === 'unavailable') ?? list.find((s) => s.status === 'stale') ?? list[0]
}
export function githubSync(pages: GitHubPage[] | undefined): Schema['SyncState'] | undefined {
  return worstSync(pages?.map((page) => page.sync) ?? [])
}

// ---------------------------------------------------------------- 开发者总览（T-0176）
// 只读本地投影：总览与权限快照都不访问 GitHub、不触发同步或测试连接（T-0175 契约）。
// 查询键都以 developer 开头并带用户 / 工作空间 / 实际筛选，SSE 的 developer 主题会一起刷新。
export type DeveloperOverviewFilters = { repo_id?: string; q?: string; range: '24h' | '7d'; cursor?: string; limit?: number }
export type DeveloperOverview = Schema['DeveloperOverviewOut']
export type RepositoryEvidence = Schema['RepositoryPermissionEvidenceOut']
export const OVERVIEW_PAGE = 12
function overviewQuery(filters: DeveloperOverviewFilters) {
  // 只放真正生效的筛选：空搜索与“全部仓库”不进键，避免同一范围出现两份缓存
  return { range: filters.range, limit: filters.limit ?? OVERVIEW_PAGE, ...(filters.repo_id ? { repo_id: filters.repo_id } : {}), ...(filters.q ? { q: filters.q } : {}), ...(filters.cursor ? { cursor: filters.cursor } : {}) }
}
export function useGitHubOverview(scope: string, filters: MaybeRefOrGetter<DeveloperOverviewFilters>, enabled: MaybeRefOrGetter<boolean> = true) {
  const auth = useAuthStore(), user = auth.user?.id, header = { 'X-Workspace-Id': scope }
  return useInfiniteQuery({
    queryKey: computed(() => ['developer', user, scope, 'overview', overviewQuery(toValue(filters))] as const),
    enabled: computed(() => toValue(enabled)),
    initialPageParam: null as string | null,
    // 筛选从查询键里取：迟到的响应只会落到它自己的键上，不会覆盖新筛选
    queryFn: async ({ queryKey, pageParam, signal }): Promise<DeveloperOverview> => {
      const query = { ...queryKey[4], ...(pageParam ? { cursor: pageParam } : {}) }
      return dataOf(await auth.api.GET('/api/v1/developer/overview', { params: { header, query }, signal }))
    },
    getNextPageParam: (page) => page.repos.next_cursor ?? undefined,
  })
}
export function useRepositoryPermissions(scope: string, repoId: MaybeRefOrGetter<string | undefined>, enabled: MaybeRefOrGetter<boolean> = true) {
  const auth = useAuthStore(), user = auth.user?.id, header = { 'X-Workspace-Id': scope }
  return useQuery({
    queryKey: computed(() => ['developer', user, scope, 'permissions', toValue(repoId) ?? ''] as const),
    enabled: computed(() => !!toValue(repoId) && toValue(enabled)),
    queryFn: async ({ queryKey, signal }): Promise<RepositoryEvidence> => dataOf(await auth.api.GET('/api/v1/developer/repos/{repo_id}/permissions', { params: { header, path: { repo_id: queryKey[4] } }, signal })),
  })
}
// 失权时清掉权限快照的缓存（不同仓库的都清），不再显示旧结果
export function permissionsKey(user: string | undefined, scope: string) { return ['developer', user, scope, 'permissions'] }
