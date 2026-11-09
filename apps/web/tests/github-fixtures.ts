import type { Schema } from '../src/services/api'
import { integration, sync } from './sprint3-fixtures'
export const github: Schema['IntegrationOut'] = { ...integration, id: '10000000-0000-4000-8000-000000000031', provider: 'github', name: '团队 GitHub', base_url: 'https://api.github.com', public_url: 'https://github.com', repositories: ['team/workbench'], tested_version: '2026-03-10', api_version: '2026-03-10', instance_version: null, webhooks: { mode: 'unavailable', registered_projects: 0, reason: 'not_supported' } }
export const repo: Schema['RepoOut'] = { id: '20000000-0000-4000-8000-000000000031', integration_id: github.id, external_id: 'github:repo:31', full_name: 'team/workbench', description: '开发工作台', private: true, archived: false, default_branch: 'main', access: 'ok', pushed_at: integration.updated_at, source_url: 'https://github.com/team/workbench', source_updated_at: integration.updated_at, synced_at: integration.updated_at }
export const issue: Schema['IssueOut'] = { ...repo, id: '30000000-0000-4000-8000-000000000031', external_id: 'github:issue:31', repo_id: repo.id, repo_full_name: repo.full_name, number: 12, title: '修复入口状态', state: 'open', state_reason: null, author: 'developer', labels: ['bug'], assignees: [], comments: 2, created_at: integration.created_at, closed_at: null, source_url: repo.source_url + '/issues/12' }
export const pull: Schema['PullOut'] = { ...issue, id: '40000000-0000-4000-8000-000000000031', external_id: 'github:pull:31', title: '增加搜索页面', draft: false, merged: false, merged_at: null, requested_reviewers: ['reviewer'], head_ref: 'feature/search', base_ref: 'main', source_url: repo.source_url + '/pull/13' }
export const run: Schema['RunOut'] = { ...repo, id: '50000000-0000-4000-8000-000000000031', external_id: 'github:run:31', repo_id: repo.id, repo_full_name: repo.full_name, name: 'Web CI', display_title: '验证搜索功能', status: 'completed', conclusion: 'failure', event: 'pull_request', head_branch: 'feature/search', head_sha: 'abc123', run_number: 31, run_attempt: 1, actor: 'developer', run_started_at: integration.updated_at, failure_summary: { failed_jobs: [{ name: 'web', failed_steps: ['Typecheck'] }] }, source_url: repo.source_url + '/actions/runs/31' }
export const checked: Schema['IntegrationTestResult'] = { ok: false, status: 'unavailable', instance_version: null, api_version: github.api_version, tested_version: github.tested_version, compatible: true, checks: [{ name: 'reachable', ok: true }, { name: 'version', ok: true }, { name: 'token', ok: true }, { name: 'webhooks', ok: false, code: 'not_supported' }], warnings: ['请缩小多余权限'], repositories: [{ full_name: repo.full_name, ok: false, checks: [{ name: 'metadata', ok: true }, { name: 'issues', ok: true }, { name: 'pull_requests', ok: true }, { name: 'actions', ok: false, code: 'missing_permission' }] }] }
export const developerPage = (items: unknown[] = [repo], state: Schema['SyncState'] = sync) => ({ items, sync: state, next_cursor: null, total_estimate: null })
// 开发者总览（T-0176）：GET /developer/overview 与 /developer/repos/{id}/permissions 的样例
export const overviewRow = (over: Partial<Schema['RepoOverviewRow']> = {}): Schema['RepoOverviewRow'] => ({ repo, open_issue_count: 1, open_pull_count: 1, failed_run_count: 1, latest_run: run, partial: false, ...over })
export function overviewOf(rows: Schema['RepoOverviewRow'][] = [overviewRow()], state: Schema['SyncState'] = sync, over: Partial<Schema['DeveloperOverviewOut']> = {}): Schema['DeveloperOverviewOut'] {
  return {
    summary: { repo_count: rows.length, open_issue_count: 1, open_pull_count: 1, failed_run_count: 1, window_start: '2026-10-23T00:00:00Z', window_end: '2026-10-30T00:00:00Z' },
    repos: { items: rows, next_cursor: null, total_estimate: rows.length },
    attention_pulls: [pull], failed_runs: [run],
    recent_updates: [{ id: issue.id, kind: 'issue', repo_id: repo.id, repo_full_name: repo.full_name, title: issue.title, source_url: issue.source_url, updated_at: '2026-10-30T08:00:00Z', time_basis: 'source_updated_at' }],
    sync: state, partial: false, ...over,
  }
}
export const allChecks: Schema['RepositoryPermissionCheck'][] = [{ name: 'metadata', ok: true }, { name: 'issues', ok: true }, { name: 'pull_requests', ok: true }, { name: 'actions', ok: true }]
export const evidenceOf = (over: Partial<Schema['RepositoryPermissionEvidenceOut']> = {}): Schema['RepositoryPermissionEvidenceOut'] => ({ repo_id: repo.id, full_name: repo.full_name, checked_at: '2026-10-30T09:00:00Z', checks: allChecks, complete: true, sync, ...over })
// 开发者页的默认接口：总览、权限快照与四个列表
export function developerRoutes(path: string) {
  if (path === '/api/v1/developer/overview') return Response.json(overviewOf())
  if (/^\/api\/v1\/developer\/repos\/[^/]+\/permissions$/.test(path)) return Response.json(evidenceOf())
  if (path.startsWith('/api/v1/developer/')) return Response.json(developerPage(path.endsWith('repos') ? [repo] : path.endsWith('issues') ? [issue] : path.endsWith('pulls') ? [pull] : [run]))
  return undefined
}
