import type { Schema } from '../../services/api'

// 集成设置页的逐仓库权限核验（T-0192，方案 docs/v2/ai-admin-redesign 规范 §6、契约 §3.2）。
// 四项结果只来自实际核验：本页刚做的“测试连接”结果（IntegrationTestResult.repositories），
// 或者 GET /developer/repos/{repo_id}/permissions 的持久快照。没有结果的项一律“未核验”，
// 绝不从 Repo.access=ok 推导成通过；metadata 失败时其余三项没有执行，也是“未核验”。

type Check = Schema['RepositoryPermissionCheck']
export type PermissionName = Check['name']
export type Evidence = Schema['RepositoryPermissionEvidenceOut']
export type Tone = 'green' | 'amber' | 'red' | 'muted' | 'blue'

export const PERMISSIONS: { name: PermissionName; label: string }[] = [
  { name: 'metadata', label: 'Metadata' }, { name: 'issues', label: 'Issues' }, { name: 'pull_requests', label: 'Pull requests' }, { name: 'actions', label: 'Actions' },
]

export type CellState = 'ok' | 'failed' | 'unknown' | 'loading' | 'error'
export type PermissionCell = { name: PermissionName; label: string; state: CellState; text: string; tone: Tone; code: string | null }
export type RowSource = 'evidence' | 'test' | 'none' | 'loading' | 'error'
export type PermissionRow = { fullName: string; cells: PermissionCell[]; source: RowSource; checkedAt: string | null; result: { text: string; tone: Tone } }

/** 本页“测试连接”的结果：只在测试后的那个连接版本上有效（之后保存、再次测试都会让它失效） */
export type TestSnapshot = { repositories: Schema['RepositoryCheck'][]; checkedAt: string | null }
/** 一个仓库的权限快照查询的状态（来自 useQueries） */
export type EvidenceState = { data?: Evidence; pending: boolean; error: unknown }

function failedText(code: string | null | undefined) {
  if (code === 'not_found') return '无法访问'
  if (code === 'missing_permission') return '缺少权限'
  return '未通过'
}
const placeholder = (state: CellState, text: string): PermissionCell[] => PERMISSIONS.map((p) => ({ ...p, state, text, tone: 'muted', code: null }))
export function cellsOf(checks: Check[]): PermissionCell[] {
  return PERMISSIONS.map((p) => {
    const found = checks.find((c) => c.name === p.name)
    if (!found) return { ...p, state: 'unknown', text: '未核验', tone: 'muted', code: null }
    if (found.ok) return { ...p, state: 'ok', text: '通过', tone: 'green', code: null }
    return { ...p, state: 'failed', text: failedText(found.code), tone: found.code === 'missing_permission' ? 'amber' : 'red', code: found.code ?? null }
  })
}
export function resultOf(cells: PermissionCell[]): { text: string; tone: Tone } {
  const failed = cells.filter((c) => c.state === 'failed')
  if (failed.length) {
    const metadata = failed.find((c) => c.name === 'metadata')
    if (metadata?.code === 'not_found') return { text: '无法访问', tone: 'red' }
    return { text: failed.some((c) => c.code === 'missing_permission') ? '需调整 Token' : '未通过', tone: 'red' }
  }
  if (cells.every((c) => c.state === 'ok')) return { text: '可读取', tone: 'green' }
  if (cells.every((c) => c.state === 'unknown')) return { text: '未核验', tone: 'muted' }
  return { text: '部分未核验', tone: 'amber' }
}

const lower = (name: string) => name.toLowerCase() // GitHub 仓库名不区分大小写
const time = (value: string | null) => (value ? Date.parse(value) : Number.NaN)

/**
 * 按当前连接配置的仓库清单逐个给出四项结果。
 * - projection：/developer/repos 的本地投影，按全名（不区分大小写）找到仓库 ID；还没同步到本地的仓库没有快照可读；
 *   投影还在读取时（projectionPending）先显示“读取中”，不把没读完的当成未核验。
 * - 快照只用属于这个仓库 ID、全名一致、有 checked_at 的结果（Token 被拒时后端返回 full_name=null，不用）。
 * - 本页测试结果比快照新时用测试结果（例如刚建好、还没同步的仓库）；时间相同以服务端快照为准。
 */
export function permissionRows(names: string[], projection: Schema['RepoOut'][], evidence: (repoId: string) => EvidenceState | undefined, test: TestSnapshot | null, projectionPending = false): PermissionRow[] {
  const byName = new Map(projection.map((repo) => [lower(repo.full_name), repo]))
  return names.map((fullName) => {
    const key = lower(fullName), repo = byName.get(key), state = repo ? evidence(repo.id) : undefined
    const snapshot = repo && state?.data && state.data.repo_id === repo.id && state.data.full_name !== null && lower(state.data.full_name) === key && state.data.checked_at && state.data.checks.length ? state.data : undefined
    const tested = test?.repositories.find((item) => lower(item.full_name) === key && item.checks.length)
    const testedAt = test?.checkedAt ? time(test.checkedAt) : Number.POSITIVE_INFINITY // 时间未知：就是本页刚做的测试
    if (tested && (!snapshot || testedAt > time(snapshot.checked_at))) {
      const cells = cellsOf(tested.checks)
      return { fullName, cells, source: 'test', checkedAt: test!.checkedAt, result: resultOf(cells) }
    }
    if (snapshot) {
      const cells = cellsOf(snapshot.checks)
      return { fullName, cells, source: 'evidence', checkedAt: snapshot.checked_at, result: resultOf(cells) }
    }
    if (state?.pending || (!repo && projectionPending)) return { fullName, cells: placeholder('loading', '读取中…'), source: 'loading', checkedAt: null, result: { text: '读取中…', tone: 'muted' } }
    if (state?.error) return { fullName, cells: placeholder('error', '读取失败'), source: 'error', checkedAt: null, result: { text: '读取失败', tone: 'muted' } }
    const cells = cellsOf([])
    return { fullName, cells, source: 'none', checkedAt: null, result: resultOf(cells) }
  })
}

export const hasFailure = (row: PermissionRow) => row.cells.some((c) => c.state === 'failed')

/** “权限核验”统计卡：缺权限的仓库数与缺的是哪几项；只数真实结果，未核验的单独说 */
export function permissionSummary(rows: PermissionRow[]): { title: string; detail: string; tone: Tone } {
  if (!rows.length) return { title: '—', detail: '尚未选择仓库', tone: 'muted' }
  if (rows.some((r) => r.source === 'loading')) return { title: '正在读取', detail: '核验结果读取中', tone: 'muted' }
  const failing = rows.filter(hasFailure)
  if (failing.length) {
    const labels = PERMISSIONS.filter((p) => failing.some((r) => r.cells.some((c) => c.name === p.name && c.state === 'failed'))).map((p) => p.label)
    return { title: `${failing.length} 个仓库缺少权限`, detail: `${labels.join('、')} 读取权限`, tone: 'amber' }
  }
  const unverified = rows.filter((r) => r.cells.some((c) => c.state !== 'ok'))
  if (unverified.length) return { title: `${unverified.length} 个仓库未核验`, detail: '测试连接后显示结果', tone: 'muted' }
  return { title: '全部可读取', detail: `${rows.length} 个仓库四项都通过`, tone: 'green' }
}

/** 连接状态：来自连接本身的状态；“部分权限不足”只在核验结果里确实有缺权限时出现 */
export function connectionStatus(item: Schema['IntegrationOut'], rows: PermissionRow[]): { text: string; tone: Tone } {
  if (item.status === 'disabled') return { text: '已停用', tone: 'muted' }
  if (item.status === 'unauthorized' || item.sync.status === 'unauthorized') return { text: '授权失效', tone: 'red' }
  if (rows.some(hasFailure)) return { text: '部分权限不足', tone: 'amber' }
  if (item.status === 'pending') return { text: '待测试', tone: 'blue' }
  if (item.status === 'unavailable') return { text: '暂不可用', tone: 'amber' }
  return { text: '已连接', tone: 'green' }
}

export function sourceText(row: PermissionRow, format: (value: string | null) => string) {
  if (row.source === 'evidence') return `上次核验 ${format(row.checkedAt)}`
  if (row.source === 'test') return row.checkedAt ? `本次测试 ${format(row.checkedAt)}` : '本次测试'
  if (row.source === 'loading') return '正在读取核验结果…'
  if (row.source === 'error') return '核验结果读取失败'
  return '尚未核验'
}
