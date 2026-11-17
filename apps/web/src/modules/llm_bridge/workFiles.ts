import type { EnvSession, WorkEntry } from './api'

// 开发环境里浏览会话工作目录（T-____）用到的小工具：文件的类型名、大小、时间，选哪个会话。

const TYPES: [RegExp, string][] = [
  [/\.(md|markdown)$/i, 'Markdown'],
  [/\.json$/i, 'JSON'],
  [/\.(ya?ml)$/i, 'YAML'],
  [/(^|\/)\.env(\..+)?$|\.(ini|toml|conf|cfg)$/i, '配置文件'],
  [/\.py$/i, 'Python'],
  [/\.tsx?$/i, 'TypeScript'],
  [/\.(m?js|cjs|jsx)$/i, 'JavaScript'],
  [/\.vue$/i, 'Vue'],
  [/\.html?$/i, 'HTML'],
  [/\.(css|scss|less)$/i, '样式表'],
  [/\.(sh|bash|zsh)$/i, 'Shell'],
  [/\.(txt|log)$/i, '文本'],
  [/\.(png|jpe?g|gif|webp|svg|ico)$/i, '图片'],
  [/\.(zip|tar|gz|tgz|7z|rar)$/i, '压缩包'],
  [/(^|\/)(Dockerfile|Makefile)$/, '构建文件'],
]
/** 列表里“类型”一栏 */
export function typeName(entry: Pick<WorkEntry, 'name' | 'kind'>) {
  if (entry.kind === 'dir') return '文件夹'
  if (entry.kind === 'link') return '链接（在工作目录以外）'
  return TYPES.find(([pattern]) => pattern.test(entry.name))?.[1] ?? '文件'
}
export const isMarkdown = (name: string) => /\.(md|markdown)$/i.test(name)

/** 字节数 → “4.2 KB” */
export function sizeText(bytes: number | null | undefined) {
  if (bytes == null) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1).replace(/\.0$/, '')} KB`
  return `${(bytes / 1024 / 1024).toFixed(1).replace(/\.0$/, '')} MB`
}

/** 修改时间：“今天 10:32”“昨天 18:40”“11月14日 09:50”，跨年写年份 */
export function timeText(at: number, now = new Date()) {
  const when = new Date(at * 1000), pad = (n: number) => String(n).padStart(2, '0')
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const diff = Math.round((day(now) - day(when)) / 86400000)
  const clock = `${pad(when.getHours())}:${pad(when.getMinutes())}`
  if (diff === 0) return `今天 ${clock}`
  if (diff === 1) return `昨天 ${clock}`
  const date = `${when.getMonth() + 1}月${when.getDate()}日`
  return when.getFullYear() === now.getFullYear() ? `${date} ${clock}` : `${when.getFullYear()}年${date}`
}

/** 默认看哪个会话的工作目录：最近活动的、还能浏览的那个 */
export const defaultSession = (sessions: EnvSession[]) => sessions.find(s => s.browsable)?.session_id ?? null

/** 仓库地址最后一段当作工作目录的名字（没有仓库时叫“工作目录”） */
export function rootName(repo: string | null | undefined) {
  const last = (repo ?? '').replace(/\.git$/, '').replace(/\/+$/, '').split('/').pop()
  return last || '工作目录'
}

/** 路径的上一级、拼接 */
export const parentOf = (path: string) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '')
export const join = (path: string, name: string) => (path ? `${path}/${name}` : name)
