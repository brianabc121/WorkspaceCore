import type { Schema } from '../../services/api'

// 开发者页的显示规则（T-0176）：未知一律显示“—”，不变成 0 或“通过”。
export const UNKNOWN = '—'
export function count(value: number | null | undefined) { return typeof value === 'number' ? String(value) : UNKNOWN }

export type Tone = 'success' | 'danger' | 'warning' | 'accent' | 'muted'
// 仓库最近一次运行的状态（只描述这个运行本身，不关联任何 PR）
export function runState(run: Schema['RunOut'] | null | undefined): { label: string; tone: Tone } | null {
  if (!run) return null
  if (run.status && run.status !== 'completed') return run.status === 'in_progress' ? { label: '运行中', tone: 'accent' } : { label: '排队中', tone: 'accent' }
  const labels: Record<string, { label: string; tone: Tone }> = {
    success: { label: '通过', tone: 'success' }, failure: { label: '失败', tone: 'danger' }, timed_out: { label: '超时', tone: 'danger' }, startup_failure: { label: '启动失败', tone: 'danger' },
    cancelled: { label: '已取消', tone: 'muted' }, skipped: { label: '已跳过', tone: 'muted' }, neutral: { label: '中性', tone: 'muted' }, action_required: { label: '需处理', tone: 'warning' }, stale: { label: '已过期', tone: 'muted' },
  }
  return labels[run.conclusion ?? ''] ?? { label: run.conclusion ?? '尚无结论', tone: 'muted' }
}

export const PERMISSION_ROWS: { name: Schema['RepositoryPermissionCheck']['name']; label: string }[] = [
  { name: 'metadata', label: '仓库元数据读取' }, { name: 'issues', label: 'Issues 读取' }, { name: 'pull_requests', label: 'Pull requests 读取' }, { name: 'actions', label: 'Actions 读取' },
]
export function checkCode(code: string | null | undefined) {
  if (code === 'missing_permission') return 'Token 缺少这项读取权限'
  if (code === 'not_found') return '仓库不存在或 Token 未获授权'
  return code ? `错误码 ${code}` : ''
}

export function windowLabel(range: '24h' | '7d') { return range === '24h' ? '近 24 小时' : '近 7 天' }

const KIND_LABELS: Record<Schema['GitHubRecentUpdate']['kind'], string> = { repo: '仓库', issue: 'Issue', pull: 'Pull request', run: 'Actions 运行' }
export function kindLabel(kind: Schema['GitHubRecentUpdate']['kind']) { return KIND_LABELS[kind] }
