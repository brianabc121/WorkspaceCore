import { z } from 'zod'
import { Newspaper, Rss } from '@lucide/vue'
import { defineModule, defineWidget } from '../types'

// 新闻早报（T-0153，docs/v2/NEWS_PLAN.md；T-0160 迁成模块）。后端清单：services/api/app/modules/news/manifest.py。
// 早报页成员与访客都能看；设置页 owner 修改、admin 只读。

// 首页“AI 新闻速览”（T-0154 的“今日早报”，T-0164 改版）的配置：没有主题数据时显示正文开头几行
export const briefingWidgetConfig = z.object({ lines: z.number().int().min(2).max(20).default(6) }).strict()
export type BriefingWidgetConfig = z.output<typeof briefingWidgetConfig>

export default defineModule({
  key: 'news',
  nav: [
    { path: '/news', title: '早报', section: '工作空间', description: '每天的新闻早报、热榜、RSS、搜索与收藏（成员与访客都能看）。', icon: Newspaper, after: '/calendar' },
    { path: '/settings/news', title: '新闻早报', section: '管理', description: '早报的推送时间、来源、兴趣主题、大模型与推送渠道（owner 修改，admin 只读）。', minRole: 'admin', icon: Rss, after: '/settings/mail' },
  ],
  routes: [
    // 早报阅读页（T-0174）用宽版内容区：正文 + 右侧栏（宿主的 main.is-wide）
    { path: '/news', component: () => import('./NewsPage.vue'), meta: { wide: true } },
    { path: '/settings/news', component: () => import('./NewsSettingsPage.vue') },
  ],
  widgets: [
    // 首页“AI 新闻速览”（T-0164，原“今日早报”）：今天早报的兴趣主题；没有主题数据时显示正文开头 lines 行
    defineWidget({ type: 'news_briefing', title: 'AI 新闻速览', defaultSize: { w: 4, h: 6 }, configSchema: briefingWidgetConfig, component: () => import('./NewsBriefingCard.vue'), refreshPolicy: 'manual', help: 'AI 新闻速览填写 lines（没有主题数据时显示早报正文开头几行，2–20）' }),
  ],
  // 早报送达、设置与收藏变化：早报页、设置页与首页“AI 新闻速览”的查询键都以 'news' 开头
  events: { news: ['news'] },
})
