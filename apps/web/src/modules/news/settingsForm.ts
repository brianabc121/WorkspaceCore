import { ApiError } from '../../services/api'
import { CHANNELS, PLATFORMS, channelName, type ChannelId, type NewsSettings, type NewsSettingsBody, type PlatformId } from './api'

// 设置 → 新闻早报的表单（T-0153，NEWS_PLAN 第 4 节）。
// 密钥只写不读：输入框留空 = 不改（PUT 里不带这个字段）；点“清除” = 发送空字符串；填了才发送新值。
export type SecretDraft = { value: string; clear: boolean }
export type FeedDraft = { id: string; name: string; url: string; enabled: boolean; max_age_days: number }
export type TopicDraft = { name: string; keywords: string; required: string; max_items: number; description: string; enabled: boolean }
export type ChannelDraft = {
  enabled: boolean; msg_type: 'markdown' | 'text'; chat_id: string; from_addr: string; to_addr: string; smtp_server: string; smtp_port: string; server_url: string
  secrets: Record<string, SecretDraft>
}
export type NewsForm = {
  enabled: boolean; push_times: string[]; briefing_mode: 'trendradar' | 'agent'; agent_instance_id: string
  platforms: PlatformId[]; newsnow_api_url: string; feeds: FeedDraft[]; topics: TopicDraft[]; exclusions: string
  filter_method: 'keyword' | 'ai'; ai_model: string; ai_api_base: string; ai_api_key: SecretDraft; ai_analysis: boolean; ai_translation: boolean
  channels: Record<ChannelId, ChannelDraft>
}

export const MAX_PUSH_TIMES = 3, MAX_FEEDS = 30, MAX_TOPICS = 20
// 推送时间：半小时为单位
export const PUSH_TIME_OPTIONS = Array.from({ length: 48 }, (_, i) => `${String(Math.floor(i / 2)).padStart(2, '0')}:${i % 2 ? '30' : '00'}`)
export const emptySecret = (): SecretDraft => ({ value: '', clear: false })
export const emptyFeed = (): FeedDraft => ({ id: '', name: '', url: '', enabled: true, max_age_days: 0 })
export const emptyTopic = (): TopicDraft => ({ name: '', keywords: '', required: '', max_items: 10, description: '', enabled: true })

// 关键词、排除词：逗号、分号或换行分隔；`/正则/` 整体算一个（正则里可以有逗号）
const SEPARATOR = /[,，;；]/
export function splitWords(text: string): string[] {
  const out: string[] = []
  const add = (word: string) => { const w = word.trim().replace(/\s+/g, ' '); if (w && !out.includes(w)) out.push(w) }
  for (const line of text.split('\n')) {
    let i = 0
    while (i < line.length) {
      while (i < line.length && (SEPARATOR.test(line[i]!) || /\s/.test(line[i]!))) i++
      if (i >= line.length) break
      if (line[i] === '/') {
        let j = i + 1
        while (j < line.length && !(line[j] === '/' && line[j - 1] !== '\\' && (j + 1 === line.length || SEPARATOR.test(line[j + 1]!) || /\s/.test(line[j + 1]!)))) j++
        if (j < line.length && j > i + 1) { add(line.slice(i, j + 1)); i = j + 1; continue }
      }
      let j = i
      while (j < line.length && !SEPARATOR.test(line[j]!)) j++
      add(line.slice(i, j)); i = j
    }
  }
  return out
}
export const joinWords = (words: readonly string[]) => words.join(', ')

export function toForm(s: NewsSettings): NewsForm {
  const channels = Object.fromEntries(CHANNELS.map(def => {
    const c = s.channels[def.id]
    return [def.id, {
      enabled: c?.enabled ?? false, msg_type: c?.msg_type ?? 'markdown', chat_id: c?.chat_id ?? '', from_addr: c?.from_addr ?? '', to_addr: c?.to_addr ?? '',
      smtp_server: c?.smtp_server ?? '', smtp_port: c?.smtp_port ? String(c.smtp_port) : '', server_url: c?.server_url ?? '',
      secrets: Object.fromEntries(def.secrets.map(f => [f.key, emptySecret()])),
    } satisfies ChannelDraft]
  })) as Record<ChannelId, ChannelDraft>
  return {
    enabled: s.enabled, push_times: s.push_times.length ? [...s.push_times] : ['07:30'], briefing_mode: s.briefing_mode,
    agent_instance_id: s.agent_instance_id ?? '', platforms: s.platforms.filter((p): p is PlatformId => PLATFORMS.some(x => x.id === p)),
    newsnow_api_url: s.newsnow_api_url, feeds: s.feeds.map(f => ({ ...f })),
    topics: s.topics.map(t => ({ name: t.name, keywords: joinWords(t.keywords ?? []), required: joinWords(t.required ?? []), max_items: t.max_items, description: t.description, enabled: t.enabled })),
    exclusions: s.exclusions.join('\n'), filter_method: s.filter_method, ai_model: s.ai_model, ai_api_base: s.ai_api_base, ai_api_key: emptySecret(),
    ai_analysis: s.ai_analysis, ai_translation: s.ai_translation, channels,
  }
}

// 填了才发送新值，点了“清除”发送空字符串，其余不带（保持不变）
const secretValue = (draft: SecretDraft) => (draft.clear ? '' : draft.value.trim() || undefined)

export function buildBody(form: NewsForm, version: number): NewsSettingsBody {
  const key = secretValue(form.ai_api_key)
  const channels = Object.fromEntries(CHANNELS.map(def => {
    const c = form.channels[def.id]
    const secrets: Record<string, string> = {}
    for (const field of def.secrets) {
      const value = secretValue(c.secrets[field.key] ?? emptySecret())
      if (value !== undefined) secrets[field.key] = value
    }
    const port = c.smtp_port.trim()
    return [def.id, {
      enabled: c.enabled, msg_type: c.msg_type, chat_id: c.chat_id.trim(), from_addr: c.from_addr.trim(), to_addr: c.to_addr.trim(),
      smtp_server: c.smtp_server.trim(), smtp_port: port ? Number(port) : null, server_url: c.server_url.trim(), secrets,
    }]
  }))
  return {
    version, enabled: form.enabled, push_times: [...new Set(form.push_times)],
    briefing_mode: form.briefing_mode, agent_instance_id: form.briefing_mode === 'agent' ? form.agent_instance_id || null : null,
    platforms: [...form.platforms], newsnow_api_url: form.newsnow_api_url.trim(),
    feeds: form.feeds.map(f => ({ id: f.id.trim(), name: f.name.trim(), url: f.url.trim(), enabled: f.enabled, max_age_days: Number(f.max_age_days) || 0 })),
    topics: form.topics.map(t => ({ name: t.name.trim(), keywords: splitWords(t.keywords), required: splitWords(t.required), max_items: Number(t.max_items) || 0, description: t.description.trim(), enabled: t.enabled })),
    exclusions: splitWords(form.exclusions), filter_method: form.filter_method, ai_model: form.ai_model.trim(), ai_api_base: form.ai_api_base.trim(),
    ...(key !== undefined ? { ai_api_key: key } : {}),
    ai_analysis: form.ai_analysis, ai_translation: form.ai_translation, channels,
  }
}

// 提交前在页面上先查一遍明显的问题（服务端还会再校验）
export function checkForm(form: NewsForm, secretsSet: (channel: ChannelId) => string[]): string[] {
  const problems: string[] = []
  if (!form.push_times.length) problems.push('至少要有一个推送时间。')
  if (new Set(form.push_times).size !== form.push_times.length) problems.push('推送时间不能重复。')
  if (form.briefing_mode === 'agent' && !form.agent_instance_id) problems.push('方案 B 要选一个智能体实例。')
  const names = new Set<string>()
  form.topics.forEach((t, i) => {
    const name = t.name.trim()
    if (!name) problems.push(`兴趣主题第 ${i + 1} 个：请填写名称。`)
    else if (/[[\]]/.test(name)) problems.push(`兴趣主题「${name}」：名称不能包含方括号。`)
    else if (names.has(name)) problems.push(`兴趣主题「${name}」重复了。`)
    names.add(name)
    if (!splitWords(t.keywords).length && !splitWords(t.required).length) problems.push(`兴趣主题「${name || i + 1}」：至少写一个关键词。`)
    if (splitWords(t.required).length > 5) problems.push(`兴趣主题「${name || i + 1}」：必含词最多 5 个。`)
    if (splitWords(t.keywords).length > 30) problems.push(`兴趣主题「${name || i + 1}」：关键词最多 30 个。`)
  })
  const ids = new Set<string>()
  form.feeds.forEach((f, i) => {
    const id = f.id.trim()
    if (!/^[a-z0-9][a-z0-9-]{0,39}$/.test(id)) problems.push(`RSS 源第 ${i + 1} 个：ID 只能用小写字母、数字和连字符，以字母或数字开头。`)
    else if (ids.has(id)) problems.push(`RSS 源的 ID「${id}」重复了。`)
    ids.add(id)
    if (!f.name.trim()) problems.push(`RSS 源第 ${i + 1} 个：请填写名称。`)
    if (!/^https?:\/\//i.test(f.url.trim())) problems.push(`RSS 源第 ${i + 1} 个：地址要以 http:// 或 https:// 开头。`)
  })
  for (const def of CHANNELS) {
    const c = form.channels[def.id]
    if (!c.enabled) continue
    const set = secretsSet(def.id)
    for (const field of def.secrets.filter(f => f.required)) {
      const draft = c.secrets[field.key] ?? emptySecret()
      if (draft.clear || (!draft.value.trim() && !set.includes(field.key))) problems.push(`推送渠道「${def.name}」已启用：请填写${field.label}。`)
    }
    if (def.id === 'email' && c.smtp_port.trim() && !/^\d{1,5}$/.test(c.smtp_port.trim())) problems.push('邮件的 SMTP 端口要是数字。')
  }
  return problems
}

// 422 的字段错误：error.details.errors[].loc / msg → “兴趣主题第 1 个 · 关键词：至少写一个关键词”
const FIELDS: Record<string, string> = {
  version: '版本', enabled: '启用', push_times: '推送时间', briefing_mode: '早报方式', agent_instance_id: '智能体实例',
  platforms: '热榜平台', newsnow_api_url: '自建 NewsNow 地址', exclusions: '全局排除词', filter_method: '筛选方式', ai_model: '模型',
  ai_api_base: '接口地址', ai_api_key: 'API Key', ai_analysis: 'AI 分析', ai_translation: '翻译',
}
const FEED_FIELDS: Record<string, string> = { id: 'ID', name: '名称', url: '地址', enabled: '启用', max_age_days: '只看最近几天' }
const TOPIC_FIELDS: Record<string, string> = { name: '名称', keywords: '关键词', required: '必含词', max_items: '每天最多几条', description: '给 AI 的描述', enabled: '启用' }
const CHANNEL_FIELDS: Record<string, string> = { enabled: '启用', msg_type: '消息格式', chat_id: '会话 ID', from_addr: '发件地址', to_addr: '收件地址', smtp_server: 'SMTP 服务器', smtp_port: 'SMTP 端口', server_url: '服务器地址' }

export function fieldLabel(loc: readonly unknown[]): string {
  const path = loc[0] === 'body' ? loc.slice(1) : [...loc]
  const [head, index, field, sub] = path
  if (head === undefined) return '设置'
  if (head === 'feeds' && typeof index === 'number') return [`RSS 源第 ${index + 1} 个`, FEED_FIELDS[String(field)]].filter(Boolean).join(' · ')
  if (head === 'topics' && typeof index === 'number') return [`兴趣主题第 ${index + 1} 个`, TOPIC_FIELDS[String(field)]].filter(Boolean).join(' · ')
  if (head === 'push_times' && typeof index === 'number') return `推送时间 ${index + 1}`
  if (head === 'channels' && typeof index === 'string') {
    const def = CHANNELS.find(c => c.id === index)
    const part = field === 'secrets' ? def?.secrets.find(s => s.key === sub)?.label ?? String(sub ?? '凭证') : field !== undefined ? CHANNEL_FIELDS[String(field)] ?? String(field) : ''
    return [`推送渠道「${channelName(index)}」`, part].filter(Boolean).join(' · ')
  }
  return FIELDS[String(head)] ?? String(head)
}

export function fieldErrors(error: unknown): string[] {
  if (!(error instanceof ApiError) || error.code !== 'VALIDATION_FAILED') return []
  const list = (error.details as { errors?: unknown } | undefined)?.errors
  if (!Array.isArray(list)) return []
  return list.flatMap((entry) => {
    if (!entry || typeof entry !== 'object') return []
    const { loc, msg } = entry as { loc?: unknown; msg?: unknown }
    const text = typeof msg === 'string' ? msg.replace(/^Value error, /, '') : '不符合要求'
    return [`${fieldLabel(Array.isArray(loc) ? loc : [])}：${text}`]
  })
}
