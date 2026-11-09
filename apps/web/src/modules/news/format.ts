import { createRenderer, sanitize } from '../../features/kb/markdown'
import type { NewsBriefing, NewsBriefingSummary } from './api'

// 早报正文（Markdown，来自 TrendRadar 或智能体）→ 消毒后的 HTML：沿用知识库的安全渲染
// （不允许原始 HTML、链接协议受限、外部图片只显示成链接、DOMPurify 消毒）；[[…]] 在早报里没有意义，按原文显示。
const render = createRenderer(() => ({ href: '#', missing: true }))
export function briefingHtml(content: string) {
  return render(content.replace(/\[\[/g, '\\[\\['))
}

// 首页“今日早报”：正文开头几行的纯文字（去掉 Markdown 标记，不渲染 HTML）
export function plainLines(content: string, count: number): string[] {
  const lines: string[] = []
  let fence = false
  for (const raw of content.split('\n')) {
    if (lines.length >= count) break
    if (/^\s*(```|~~~)/.test(raw)) { fence = !fence; continue }
    let line = raw.trim()
    if (!fence) {
      if (/^([-*_]\s*){3,}$/.test(line) || /^\|?[\s:|-]+\|[\s:|-]*$/.test(line)) continue // 分隔线、表格的对齐行
      line = line
        .replace(/^#{1,6}\s+/, '').replace(/^(>\s?)+/, '').replace(/^([-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?/, '')
        .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
        .replace(/<[^>]*>/g, '').replace(/(\*\*|~~|`)/g, '').replace(/(^|[^\w])__(.+?)__(?![\w])/g, '$1$2')
        .replace(/(^|\s)[*_]([^*_]+)[*_](?=\s|$|[，。！？、,.!?])/g, '$1$2')
        .replace(/^\|\s*|\s*\|$/g, '').replace(/\s*\|\s*/g, ' · ')
        .trim()
    }
    if (line) lines.push(line)
  }
  return lines
}

export function briefingWhen(briefing: Pick<NewsBriefing | NewsBriefingSummary, 'local_date' | 'slot'>) {
  const day = briefing.local_date.replaceAll('-', '/')
  return briefing.slot ? `${day} ${briefing.slot}` : day
}

// 往期入口里的短日期：用早报自己的 local_date（部署时区的日期），不用浏览器的 UTC 日期
export function briefingDay(briefing: Pick<NewsBriefing | NewsBriefingSummary, 'local_date' | 'slot'>) {
  const [, month = '', day = ''] = briefing.local_date.split('-')
  const date = month && day ? `${Number(month)}月${Number(day)}日` : briefing.local_date
  return briefing.slot ? `${date} ${briefing.slot}` : date
}

// 早报阅读页（T-0174，二批改造规范第 5 节）：正文按真实标题分节。
// - 先整篇安全渲染（briefingHtml），再按顶层标题切开：取出现至少两次的最高一级 h1–h3；没有就取唯一的那个标题；
//   没有标题就是一整段。真实有几节就几节，不硬凑“3 件事”。
// - 每节的“来源”只取这一节正文里的 http(s) 链接（显示域名）；外部图片的替代链接、站内链接不算来源。
// - 切开后的片段再消毒一次，再交给 v-html。
export type BriefingSection = { html: string; titled: boolean; hosts: string[]; link: string | null }

const HEADING = /^H([1-3])$/
const levelOf = (node: Node) => (node.nodeType === 1 ? Number(HEADING.exec((node as Element).tagName)?.[1] ?? 0) : 0)
const meaningful = (node: Node) => node.nodeType === 1 || !!node.textContent?.trim()
function hostOf(href: string) {
  try { return new URL(href).hostname.replace(/^www\./, '') } catch { return '' }
}

export function briefingSections(content: string): BriefingSection[] {
  if (!content.trim()) return []
  const template = document.createElement('template')
  template.innerHTML = briefingHtml(content)
  const nodes = [...template.content.childNodes]
  const counts = new Map<number, number>()
  for (const node of nodes) {
    const level = levelOf(node)
    if (level) counts.set(level, (counts.get(level) ?? 0) + 1)
  }
  const levels = [...counts.keys()].sort((a, b) => a - b)
  const split = levels.find((level) => (counts.get(level) ?? 0) >= 2) ?? levels[0] ?? 0
  const groups: { titled: boolean; nodes: Node[] }[] = []
  let current: { titled: boolean; nodes: Node[] } = { titled: false, nodes: [] }
  for (const node of nodes) {
    if (split && levelOf(node) === split) {
      if (current.nodes.some(meaningful)) groups.push(current)
      current = { titled: true, nodes: [node] }
    } else current.nodes.push(node)
  }
  if (current.nodes.some(meaningful)) groups.push(current)
  return groups.map((group) => {
    const box = document.createElement('div')
    box.append(...group.nodes)
    const links = [...new Set([...box.querySelectorAll('a[href]')]
      .filter((a) => !a.classList.contains('kb-image-link') && /^https?:\/\//i.test(a.getAttribute('href') ?? ''))
      .map((a) => a.getAttribute('href')!))]
    const hosts = [...new Set(links.map(hostOf).filter(Boolean))]
    return { html: sanitize(box.innerHTML), titled: group.titled, hosts, link: links.length === 1 ? links[0]! : null }
  })
}
