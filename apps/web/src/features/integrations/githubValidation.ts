// 与后端 MAX_GITHUB_REPOSITORIES 一致
export const MAX_REPOSITORIES = 20

// 只接受细粒度 Personal Access Token（github_pat_ 开头），经典 Token 权限过大（T-0091）
export const TOKEN_PATTERN = /^github_pat_.+$/
export const CLASSIC_TOKEN_MESSAGE = '请使用细粒度 Token（github_pat_ 开头），不接受经典 Token。'
// 生成 Token 的页面：预填名称、90 天有效期与只读权限（Issues、Pull requests、Actions；Metadata 总是只读），T-0091
export const TOKEN_DAYS = 90
export const TOKEN_URL = 'https://github.com/settings/personal-access-tokens/new?name=My+Workspace&description=My+Workspace+read-only+sync&expires_in=90&issues=read&pull_requests=read&actions=read'
// 已有的细粒度 Token 列表（编辑权限、换仓库）
export const TOKEN_SETTINGS_URL = 'https://github.com/settings/personal-access-tokens'

export function repositoryList(value: string): string[] {
  const names = [...new Set(value.split(/[\s,]+/).filter(Boolean).map((name) => name.toLowerCase()))]
  if (names.length < 1 || names.length > MAX_REPOSITORIES) throw new Error(`请填写 1–${MAX_REPOSITORIES} 个仓库，每行一个 owner/repo。`)
  if (names.some((name) => !/^[a-z0-9-]{1,39}\/[a-z0-9._-]{1,100}$/.test(name) || ['.', '..'].includes(name.split('/')[1]!))) throw new Error('仓库格式应为 owner/repo，不要填写网址。')
  return names
}
