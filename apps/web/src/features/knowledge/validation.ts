import type { Schema } from '../../services/api'
export type BookmarkFields = Omit<Schema['BookmarkCreate'], 'visibility'> & Partial<Pick<Schema['BookmarkCreate'], 'visibility'>>
const documentId = /^[A-Za-z0-9_-]{1,64}$/
const reserved = new Set(['all', 'collection', 'tag', 'trash', 'journals', 'settings', 'chat'])
export function sourceUrlError(value: string): string {
  try {
    const url = new URL(value)
    if (!['http:', 'https:'].includes(url.protocol)) return '知识来源只支持 HTTP 或 HTTPS 地址。'
    if (url.username || url.password) return '地址不能包含用户名或密码。'
    if (url.search || url.hash) return '知识来源地址不能包含查询参数或片段。'
    const host = url.hostname.replace(/^\[|\]$/g, '').toLowerCase()
    if (/^169\.254\./.test(host) || /^fe[89ab][0-9a-f]:/.test(host) || ['0.0.0.0', '::'].includes(host) || /^::ffff:a9fe:/.test(host)) return '不能使用链路本地、云元数据或未指定地址。'
    return value.length > 1024 ? '知识来源地址过长。' : ''
  } catch { return '请输入完整有效的知识来源地址。' }
}
export function bookmarkUrlError(value: string, baseUrl: string): string {
  try {
    if (value.length > 2048) return '文档链接不能超过 2048 个字符。'
    const url = new URL(value), base = new URL(baseUrl)
    if (!['http:', 'https:'].includes(url.protocol)) return '文档链接只支持 HTTP 或 HTTPS。'
    if (url.username || url.password) return '文档链接不能包含用户名或密码。'
    const prefix = base.pathname.replace(/\/+$/, '')
    if (url.origin !== base.origin || !url.pathname.startsWith(`${prefix}/`)) return '文档地址必须属于已配置的 AFFiNE 来源及其子路径。'
    const parts = url.pathname.slice(prefix.length).split('/')
    if (parts.length !== 4 || !['workspace', 'share'].includes(parts[1]!) || reserved.has(parts[3]!)) return '请粘贴具体文档的 workspace 或 share 链接，不要填写功能页地址。'
    if (!documentId.test(parts[2]!) || !documentId.test(parts[3]!)) return '文档或 AFFiNE 工作空间 ID 格式无效。'
    return ''
  } catch { return '请输入完整有效的 AFFiNE 文档链接。' }
}
