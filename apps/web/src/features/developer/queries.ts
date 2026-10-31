import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { useInfiniteQuery } from '@tanstack/vue-query'
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
export function githubSync(pages: GitHubPage[] | undefined): Schema['SyncState'] | undefined {
  const states = pages?.map((page) => page.sync) ?? []
  return states.find((s) => s.status === 'unauthorized') ?? states.find((s) => s.status === 'not_configured') ?? states.find((s) => s.reason === 'rate_limited') ?? states.find((s) => s.status === 'unavailable') ?? states.find((s) => s.status === 'stale') ?? states[0]
}
