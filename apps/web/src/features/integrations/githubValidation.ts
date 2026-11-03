// 与后端 MAX_GITHUB_REPOSITORIES 一致
export const MAX_REPOSITORIES = 20

export function repositoryList(value: string): string[] {
  const names = [...new Set(value.split(/[\s,]+/).filter(Boolean).map((name) => name.toLowerCase()))]
  if (names.length < 1 || names.length > MAX_REPOSITORIES) throw new Error(`请填写 1–${MAX_REPOSITORIES} 个仓库，每行一个 owner/repo。`)
  if (names.some((name) => !/^[a-z0-9-]{1,39}\/[a-z0-9._-]{1,100}$/.test(name) || ['.', '..'].includes(name.split('/')[1]!))) throw new Error('仓库格式应为 owner/repo，不要填写网址。')
  return names
}
