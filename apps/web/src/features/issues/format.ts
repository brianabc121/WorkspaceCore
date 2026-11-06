import { createRenderer } from '../kb/markdown'

// Issue 正文与评论 → 消毒后的 HTML（T-0124，方案 8.8）：
// - 沿用知识库的安全 Markdown 渲染（不允许原始 HTML，链接协议受限，DOMPurify 消毒）；
// - [[…]] 在 Issue 里没有意义，按原文显示；
// - 文字里的 #12 链到同一项目的 Issue，@名字 标成提及（代码与链接里的不算）。
const render = createRenderer(() => ({ href: '#', missing: true }))
const REF = /(^|[^\w&])#(\d{1,7})\b|(^|[^\w@])@([^\s@#,，。:：;；()（）[\]<>]{1,40})/g

export function issueHtml(body: string, projectId: string) {
  const html = render(body.replace(/\[\[/g, '\\[\\['))
  if (typeof DOMParser === 'undefined') return html
  const doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html')
  const root = doc.body.firstElementChild!
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const texts: Text[] = []
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!(node.parentElement?.closest('code, pre, a'))) texts.push(node as Text)
  }
  for (const text of texts) {
    const value = text.data
    REF.lastIndex = 0
    if (!REF.test(value)) continue
    REF.lastIndex = 0
    const parts = doc.createDocumentFragment()
    let cursor = 0
    for (const match of value.matchAll(REF)) {
      const lead = match[1] ?? match[3] ?? ''
      const start = match.index! + lead.length
      parts.append(value.slice(cursor, start))
      if (match[2]) {
        const link = doc.createElement('a')
        link.href = `/projects/${encodeURIComponent(projectId)}/issues/${match[2]}`
        link.className = 'issue-ref'
        link.textContent = `#${match[2]}`
        parts.append(link)
      } else {
        const mention = doc.createElement('span')
        mention.className = 'issue-mention'
        mention.textContent = `@${match[4]}`
        parts.append(mention)
      }
      cursor = match.index! + match[0].length
    }
    parts.append(value.slice(cursor))
    text.replaceWith(parts)
  }
  return root.innerHTML
}
