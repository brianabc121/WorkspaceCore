import { z } from 'zod'
import { Newspaper, Rss } from '@lucide/vue'
import { defineModule, defineWidget } from '../types'

// 新闻早报（T-0153，docs/v2/NEWS_PLAN.md；T-0160 迁成模块）。后端清单：services/api/app/modules/news/manifest.py。
// 早报页成员与访客都能看；设置页 owner 修改、admin 只读。

// 首页“今日早报”（T-0154）的配置：显示正文开头几行
export const briefingWidgetConfig = z.object({ lines: z.number().int().min(2).max(20).default(6) }).strict()
export type BriefingWidgetConfig = z.output<typeof briefingWidgetConfig>

export default defineModule({
  key: 'news',
  nav: [
    { path: '/news', title: '早报', section: '工作空间', description: '每天的新闻早报、热榜、RSS、搜索与收藏（成员与访客都能看）。', icon: Newspaper, after: '/calendar' },
    { path: '/settings/news', title: '新闻早报', section: '管理', description: '早报的推送时间、来源、兴趣主题、大模型与推送渠道（owner 修改，admin 只读）。', minRole: 'admin', icon: Rss, after: '/settings/mail' },
  ],
  routes: [
    { path: '/news', component: () => import('./NewsPage.vue') },
    { path: '/settings/news', component: () => import('./NewsSettingsPage.vue') },
  ],
  widgets: [
    defineWidget({ type: 'news_briefing', title: '今日早报', defaultSize: { w: 6, h: 4 }, configSchema: briefingWidgetConfig, component: () => import('./NewsBriefingCard.vue'), refreshPolicy: 'manual', help: '今日早报填写 lines（显示正文开头几行，2–20）' }),
  ],
  // 早报送达、设置与收藏变化：早报页、设置页与首页“今日早报”的查询键都以 'news' 开头
  events: { news: ['news'] },
})
