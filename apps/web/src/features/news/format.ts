import { createRenderer } from '../kb/markdown'
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
