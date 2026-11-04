import MarkdownIt, { type StateCore, type StateInline, type Token } from 'markdown-it'
import DOMPurify from 'dompurify'

// 笔记正文的渲染（T-0099）：Markdown → HTML → DOMPurify 消毒后才放进页面。
// - 不允许原始 HTML（html: false），链接协议只允许 http / https / mailto 与站内路径；
// - [[标题]]、[[标题|显示]]、[[标题#小节]]、[[知识库/标题]]、[[task|project|bookmark:<id>]] 渲染成站内链接，
//   代码里的不算（与后端 app/domain/knowledge_links.py 一致）；
// - 外部图片不自动加载（会泄露访问记录），显示成链接；
// - “- [ ] / - [x]”显示成只读的勾选框。

export type WikiTarget = { text: string; display: string; section: string }
export type Resolved = { href: string; missing?: boolean; title?: string }
export type Resolver = (target: WikiTarget) => Resolved

// 与后端 normalize 一致：全角转半角（NFKC）、转小写、合并空白
export const normalize = (value: string) => value.normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim()

export function parseTarget(inner: string): WikiTarget | null {
  const [rawTarget = '', ...rest] = inner.split('|')
  const display = rest.join('|').trim()
  const hash = rawTarget.indexOf('#')
  const text = (hash >= 0 ? rawTarget.slice(0, hash) : rawTarget).trim()
  const section = hash >= 0 ? rawTarget.slice(hash + 1).trim() : ''
  if (!text) return null
  return { text, display: display || (section ? `${text} › ${section}` : text.split('/').pop() || text), section }
}

export const headingId = (text: string) => 'kb-h-' + normalize(text).replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '')

function wikilink(state: StateInline, silent: boolean) {
  const start = state.pos
  if (state.src.charCodeAt(start) !== 0x5b || state.src.charCodeAt(start + 1) !== 0x5b) return false
  const end = state.src.indexOf(']]', start + 2)
  if (end < 0) return false
  const inner = state.src.slice(start + 2, end)
  if (inner.includes('[') || inner.includes('\n')) return false
  const target = parseTarget(inner)
  if (!target) return false
  if (!silent) {
    const token = state.push('kb_wikilink', '', 0)
    token.meta = target
  }
  state.pos = end + 2
  return true
}

function taskItems(state: StateCore) {
  const tokens = state.tokens
  for (let i = 2; i < tokens.length; i++) {
    const inline = tokens[i]
    if (inline.type !== 'inline' || tokens[i - 1].type !== 'paragraph_open' || tokens[i - 2].type !== 'list_item_open') continue
    const match = /^\[([ xX])\]\s+/.exec(inline.content)
    const first = inline.children?.[0]
    if (!match || !first || first.type !== 'text' || !/^\[[ xX]\]/.test(first.content)) continue
    first.content = first.content.replace(/^\[[ xX]\]\s*/, '')
    const box = new state.Token('kb_task', '', 0)
    box.meta = { checked: match[1] !== ' ' }
    inline.children!.unshift(box)
    tokens[i - 2].attrJoin('class', 'kb-task')
  }
}

export function createRenderer(resolve: Resolver) {
  const md = new MarkdownIt({ html: false, linkify: true, breaks: false, typographer: false })
  md.validateLink = (url: string) => /^(https?:|mailto:|\/(?!\/)|#)/i.test(url.trim())
  md.inline.ruler.before('link', 'kb_wikilink', wikilink)
  // 放在最后（text_join 之后）：这时“[x] ”已经合并成一段文字
  md.core.ruler.push('kb_task', taskItems)
  md.renderer.rules.kb_wikilink = (tokens, idx) => {
    const target = tokens[idx].meta as WikiTarget
    const found = resolve(target)
    const cls = found.missing ? 'kb-wl is-missing' : 'kb-wl'
    const title = found.missing ? `还没有《${target.text}》，点击新建` : (found.title ?? target.text)
    return `<a class="${cls}" href="${md.utils.escapeHtml(found.href)}" title="${md.utils.escapeHtml(title)}">${md.utils.escapeHtml(target.display)}</a>`
  }
  md.renderer.rules.kb_task = (tokens, idx) => `<input type="checkbox" disabled${(tokens[idx].meta as { checked: boolean }).checked ? ' checked' : ''} aria-label="${(tokens[idx].meta as { checked: boolean }).checked ? '已完成' : '未完成'}"> `
  md.renderer.rules.heading_open = (tokens, idx, options, _env, self) => {
    const inline: Token | undefined = tokens[idx + 1]
    if (inline?.type === 'inline') tokens[idx].attrSet('id', headingId(inline.content))
    return self.renderToken(tokens, idx, options)
  }
  // 本工作空间的图片（/api/v1/knowledge/files/<id>，T-0103）直接显示；外部网址的图片不自动加载，只显示链接
  md.renderer.rules.image = (tokens, idx) => {
    const token = tokens[idx], src = String(token.attrGet('src') ?? ''), alt = token.content || '图片'
    if (KB_IMAGE.test(src)) return `<img class="kb-img" src="${md.utils.escapeHtml(src)}" alt="${md.utils.escapeHtml(alt)}" loading="lazy">`
    return `<a class="kb-image-link" href="${md.utils.escapeHtml(src)}" rel="noopener noreferrer nofollow" target="_blank">图片：${md.utils.escapeHtml(alt)}</a>`
  }
  const defaultLink = md.renderer.rules.link_open ?? ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))
  md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
    const href = String(tokens[idx].attrGet('href') ?? '')
    if (/^(https?:|mailto:)/i.test(href)) { tokens[idx].attrSet('target', '_blank'); tokens[idx].attrSet('rel', 'noopener noreferrer nofollow') }
    return defaultLink(tokens, idx, options, env, self)
  }
  return (source: string) => sanitize(md.render(source))
}

const KB_IMAGE = /^\/api\/v1\/knowledge\/files\/[0-9a-fA-F-]{36}$/
const PURIFY = {
  ALLOWED_TAGS: ['a', 'p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'em', 's', 'del', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'input', 'img'],
  ALLOWED_ATTR: ['href', 'title', 'class', 'id', 'target', 'rel', 'type', 'checked', 'disabled', 'aria-label', 'start', 'style', 'src', 'alt', 'loading'],
}
const SAFE_HREF = /^(https?:|mailto:|\/(?!\/)|#)/i
// 单独的 DOMPurify 实例，钩子不影响别处：链接只留 http / https / mailto / 站内；style 只留表格对齐；input 只能是只读勾选框；
// img 只能是本工作空间的图片
let purifier: ReturnType<typeof DOMPurify> | null = null
function instance() {
  if (purifier) return purifier
  purifier = DOMPurify(window)
  purifier.addHook('afterSanitizeAttributes', (node) => {
    const href = node.getAttribute('href')
    if (href !== null && !SAFE_HREF.test(href.trim())) node.removeAttribute('href')
    const style = node.getAttribute('style')
    if (style !== null && !/^text-align:\s*(left|center|right);?$/.test(style.trim())) node.removeAttribute('style')
    if (node.tagName === 'INPUT') { node.setAttribute('type', 'checkbox'); node.setAttribute('disabled', '') }
    // 图片只能是本工作空间的图片地址（经工作台读取），其他来源去掉 src
    if (node.tagName === 'IMG' && !KB_IMAGE.test((node.getAttribute('src') ?? '').trim())) node.removeAttribute('src')
    if (node.tagName !== 'IMG' && node.hasAttribute('src')) node.removeAttribute('src')
  })
  return purifier
}
export function sanitize(html: string) {
  return instance().sanitize(html, { ...PURIFY, RETURN_TRUSTED_TYPE: false }) as string
}

// 大纲：正文里的 # 标题（跳过代码块）
export function outline(source: string) {
  const items: { level: number; text: string; id: string; line: number }[] = []
  let fence = false
  source.split('\n').forEach((line, index) => {
    if (/^\s*(```|~~~)/.test(line)) { fence = !fence; return }
    const match = !fence && /^(#{1,4})\s+(.+?)\s*#*\s*$/.exec(line)
    if (match) items.push({ level: match[1].length, text: match[2], id: headingId(match[2]), line: index + 1 })
  })
  return items
}

// 统计：字数（中文按字、英文按词）与 [[链接]] 个数
export function stats(source: string) {
  const text = source.replace(/```[\s\S]*?```/g, '')
  const words = (text.match(/[一-鿿]/g)?.length ?? 0) + (text.replace(/[一-鿿]/g, ' ').match(/[A-Za-z0-9_]+/g)?.length ?? 0)
  return { words, links: text.match(/\[\[[^[\]\n]+\]\]/g)?.length ?? 0 }
}

// 正文里的行内 #标签（不在代码里、不是标题行）：保存时并入标签
export function inlineTags(source: string) {
  const found = new Set<string>()
  let fence = false
  for (const line of source.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) { fence = !fence; continue }
    if (fence || /^\s{0,3}#{1,6}\s/.test(line)) continue
    for (const m of line.replace(/`[^`]*`/g, '').matchAll(/(?:^|\s)#([^\s#.,;:!?，。；：！？、()（）[\]]{1,40})/g)) found.add(m[1])
  }
  return [...found]
}
