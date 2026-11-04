import { EditorSelection, EditorState, type Extension } from '@codemirror/state'
import { Decoration, EditorView, MatchDecorator, ViewPlugin, drawSelection, highlightActiveLine, keymap, placeholder, type DecorationSet, type ViewUpdate } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab, redo, undo } from '@codemirror/commands'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { markdown, markdownLanguage } from '@codemirror/lang-markdown'
import { autocompletion, completionKeymap, type Completion, type CompletionContext, type CompletionResult } from '@codemirror/autocomplete'
import { highlightSelectionMatches, searchKeymap } from '@codemirror/search'
import { tags as t } from '@lezer/highlight'

// 笔记编辑器（T-0099，方案 6.3）：CodeMirror 6 + Markdown。
// - 输入 [[ 弹出笔记补全（标题与别名，跨知识库），回车插入 [[标题]]；没有的可以直接写，保存后成为待创建的链接；
// - 行内 #标签 弹出已有标签；
// - 工具栏的格式命令都在这里，撤销 / 重做走 CodeMirror 的历史。
export type Suggestion = { title: string; detail: string; insert: string }
export type EditorHooks = {
  onChange: (doc: string) => void
  suggest: (query: string) => Promise<Suggestion[]>
  tags: () => string[]
  onSave?: () => void
}

const highlight = HighlightStyle.define([
  { tag: t.heading1, fontWeight: '700', fontSize: '1.3em' },
  { tag: t.heading2, fontWeight: '700', fontSize: '1.15em' },
  { tag: [t.heading3, t.heading4, t.heading5, t.heading6], fontWeight: '700' },
  { tag: t.strong, fontWeight: '700' },
  { tag: t.emphasis, fontStyle: 'italic' },
  { tag: t.strikethrough, textDecoration: 'line-through' },
  { tag: [t.link, t.url], color: 'var(--kb-accent)' },
  { tag: t.monospace, fontFamily: 'ui-monospace, Menlo, Consolas, monospace', color: 'var(--kb-agent)' },
  { tag: [t.processingInstruction, t.meta, t.contentSeparator], color: 'var(--kb-muted)' },
  { tag: t.quote, color: 'var(--kb-muted)' },
])

// [[链接]] 高亮
const wikiDecorator = new MatchDecorator({ regexp: /\[\[[^[\]\n]+\]\]/g, decoration: Decoration.mark({ class: 'kb-cm-link' }) })
const wikiLinks = ViewPlugin.fromClass(class {
  decorations: DecorationSet
  constructor(view: EditorView) { this.decorations = wikiDecorator.createDeco(view) }
  update(update: ViewUpdate) { this.decorations = wikiDecorator.updateDeco(update, this.decorations) }
}, { decorations: v => v.decorations })

function wikiSource(hooks: EditorHooks) {
  return async (context: CompletionContext): Promise<CompletionResult | null> => {
    const match = context.matchBefore(/\[\[[^\]\n|#]*$/)
    if (!match) return null
    const query = match.text.slice(2)
    if (!query.trim()) return context.explicit ? { from: match.from + 2, options: [], filter: false } : null
    let items: Suggestion[] = []
    try { items = await hooks.suggest(query.trim()) } catch { items = [] }
    if (context.aborted) return null
    const after = context.state.sliceDoc(context.pos, context.pos + 2)
    const close = after === ']]' ? '' : ']]'
    const options: Completion[] = items.map((s, i) => ({ label: s.title, detail: s.detail, boost: 50 - i, apply: s.insert + close }))
    if (!items.some(s => s.title === query.trim())) options.push({ label: `新建《${query.trim()}》`, detail: '先写链接，以后再建这篇', boost: -99, apply: query.trim() + close })
    return { from: match.from + 2, options, filter: false }
  }
}

function tagSource(hooks: EditorHooks) {
  return (context: CompletionContext): CompletionResult | null => {
    const match = context.matchBefore(/(?:^|\s)#[^\s#]*$/)
    if (!match) return null
    const start = match.text.indexOf('#') + match.from
    const line = context.state.doc.lineAt(context.pos)
    if (start === line.from) return null // 行首的 # 是标题
    const query = context.state.sliceDoc(start + 1, context.pos).toLowerCase()
    const options = hooks.tags().filter(tag => tag.toLowerCase().includes(query)).slice(0, 20).map(tag => ({ label: '#' + tag, apply: '#' + tag + ' ' }))
    return options.length ? { from: start, options, filter: false } : null
  }
}

export function createEditor(parent: HTMLElement, doc: string, hooks: EditorHooks, extra: Extension[] = []) {
  const view = new EditorView({
    parent,
    state: EditorState.create({
      doc,
      extensions: [
        history(), drawSelection(), highlightActiveLine(), highlightSelectionMatches(), EditorView.lineWrapping,
        markdown({ base: markdownLanguage }), syntaxHighlighting(highlight), wikiLinks,
        autocompletion({ override: [wikiSource(hooks), tagSource(hooks)], icons: false, activateOnTyping: true }),
        placeholder('从这里开始写。输入 [[ 链接到别的笔记'),
        keymap.of([
          { key: 'Mod-s', preventDefault: true, run: () => { hooks.onSave?.(); return true } },
          { key: 'Mod-b', run: v => wrap(v, '**') },
          { key: 'Mod-i', run: v => wrap(v, '*') },
          ...completionKeymap, ...searchKeymap, ...historyKeymap, ...defaultKeymap, indentWithTab,
        ]),
        EditorView.updateListener.of(u => { if (u.docChanged) hooks.onChange(u.state.doc.toString()) }),
        EditorView.contentAttributes.of({ 'aria-label': 'Markdown 编辑区', 'aria-multiline': 'true', spellcheck: 'false' }),
        ...extra,
      ],
    }),
  })
  return view
}

// 格式命令（工具栏与快捷键共用）
export function wrap(view: EditorView, mark: string, placeholderText = '文字') {
  view.dispatch(view.state.changeByRange(range => {
    const text = view.state.sliceDoc(range.from, range.to) || placeholderText
    const insert = mark + text + mark
    return { changes: { from: range.from, to: range.to, insert }, range: EditorSelection.range(range.from + mark.length, range.from + mark.length + text.length) }
  }))
  view.focus()
  return true
}

export function linePrefix(view: EditorView, prefix: string | ((index: number) => string)) {
  const { state } = view
  const lines = new Set<number>()
  for (const r of state.selection.ranges) for (let n = state.doc.lineAt(r.from).number; n <= state.doc.lineAt(r.to).number; n++) lines.add(n)
  const changes = [...lines].map((n, i) => {
    const line = state.doc.line(n)
    const p = typeof prefix === 'function' ? prefix(i) : prefix
    const existing = /^(#{1,6} |> |- \[[ xX]\] |- |\d+\. )/.exec(line.text)?.[0] ?? ''
    return existing === p ? { from: line.from, to: line.from + existing.length, insert: '' } : { from: line.from, to: line.from + existing.length, insert: p }
  })
  view.dispatch({ changes })
  view.focus()
  return true
}

export function insertText(view: EditorView, text: string, cursorOffset = text.length) {
  const { from, to } = view.state.selection.main
  view.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + cursorOffset } })
  view.focus()
  return true
}

export function insertBlock(view: EditorView, block: string) {
  const { from } = view.state.selection.main
  const line = view.state.doc.lineAt(from)
  const before = line.text.trim() ? '\n\n' : ''
  const at = line.text.trim() ? line.to : line.from
  view.dispatch({ changes: { from: at, insert: before + block + '\n' }, selection: { anchor: at + before.length + block.length } })
  view.focus()
  return true
}

export const runUndo = (view: EditorView) => undo(view)
export const runRedo = (view: EditorView) => redo(view)

export function scrollToLine(view: EditorView, lineNumber: number) {
  const line = view.state.doc.line(Math.min(Math.max(lineNumber, 1), view.state.doc.lines))
  view.dispatch({ selection: { anchor: line.from }, effects: EditorView.scrollIntoView(line.from, { y: 'start', yMargin: 24 }) })
  view.focus()
}

export function setDoc(view: EditorView, doc: string) {
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: doc } })
}
