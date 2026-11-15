import { createRenderer } from '../../../features/kb/markdown'

// 回答按 Markdown 显示（docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 的 C2）：标题、列表、表格、链接、行内代码。
// 沿用知识库的安全渲染：不允许原始 HTML，链接只允许 http / https / mailto，外部图片不自动加载（显示成链接），
// 结果再用 DOMPurify 消毒。模型写的内容是不可信的：这里只排版，不执行任何东西。
// 三个反引号的代码块不走这里：聊天页自己拆出来，带语言名和“复制”按钮（chatApi.ts 的 splitText）。
const render = createRenderer(() => ({ href: '#', missing: true }))

/** 一段回答 → 消毒后的 HTML。[[…]] 在这里没有意义，按原文显示 */
export const answerHtml = (text: string) => render(text.replace(/\[\[/g, '\\[\\['))
