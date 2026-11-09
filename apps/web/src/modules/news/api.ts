import { computed, type Ref } from 'vue'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, errorMessage, type Schema } from '../../services/api'

// 新闻早报（T-0153，docs/v2/NEWS_PLAN.md 第 4、5、7、8 节）。
// - 早报、热榜、RSS、搜索、收藏：成员与访客都能看；收藏 / 不感兴趣要 member（访客不显示按钮，后端也会拒绝）。
// - 设置：owner 与 admin 能看，只有 owner 能改、能发测试推送、能让智能体现在写一份。
// 条目的标题、链接、来源名都来自第三方：页面按纯文字显示，早报正文按安全模式渲染（features/kb/markdown.ts）。
// 查询键都以 'news' 开头，SSE 的 news 主题刷新它们（index.ts 的 events）。
export type NewsItem = Schema['NewsItemOut']
export type NewsItemList = Schema['NewsItemList']
export type NewsBriefing = Schema['NewsBriefingOut']
export type NewsBriefingSummary = Schema['NewsBriefingSummary']
export type NewsToday = Schema['NewsTodayOut']
export type NewsFeedback = Schema['NewsFeedbackOut']
export type NewsSettings = Schema['NewsSettingsOut']
export type NewsSettingsBody = Schema['NewsSettingsIn']
export type NewsStatus = Schema['NewsStatusOut']
export type PlatformId = Schema['NewsPlatformChoice']['id']
export type ChannelId = 'feishu' | 'dingtalk' | 'wework' | 'telegram' | 'email' | 'ntfy' | 'bark' | 'slack' | 'webhook'

// 核验过的 11 个热榜平台（后端 PlatformId）；平台列表接口只有 admin 能看，热榜页用这份
export const PLATFORMS: readonly { id: PlatformId; name: string }[] = [
  { id: 'toutiao', name: '今日头条' }, { id: 'baidu', name: '百度热搜' }, { id: 'wallstreetcn-hot', name: '华尔街见闻' },
  { id: 'thepaper', name: '澎湃新闻' }, { id: 'bilibili-hot-search', name: 'bilibili 热搜' }, { id: 'cls-hot', name: '财联社热门' },
  { id: 'ifeng', name: '凤凰网' }, { id: 'tieba', name: '贴吧' }, { id: 'weibo', name: '微博' }, { id: 'douyin', name: '抖音' }, { id: 'zhihu', name: '知乎' },
]

export type PlainField = 'msg_type' | 'chat_id' | 'from_addr' | 'to_addr' | 'smtp_server' | 'smtp_port' | 'server_url'
export type ChannelDefinition = {
  id: ChannelId; name: string; help: string
  plain: { key: PlainField; label: string; placeholder?: string }[]
  secrets: { key: string; label: string; required: boolean; placeholder?: string }[]
}
// 推送渠道（后端 news_config.CHANNEL_SECRETS）：明文字段直接保存，密钥字段加密保存、只写不读
export const CHANNELS: readonly ChannelDefinition[] = [
  { id: 'feishu', name: '飞书', help: '群机器人的 Webhook 地址。', plain: [], secrets: [{ key: 'webhook_url', label: 'Webhook 地址', required: true, placeholder: 'https://open.feishu.cn/open-apis/bot/v2/hook/…' }] },
  { id: 'dingtalk', name: '钉钉', help: '群机器人的 Webhook 地址。', plain: [], secrets: [{ key: 'webhook_url', label: 'Webhook 地址', required: true, placeholder: 'https://oapi.dingtalk.com/robot/send?access_token=…' }] },
  { id: 'wework', name: '企业微信', help: '群机器人的 Webhook 地址；个人微信用“文本”格式。', plain: [{ key: 'msg_type', label: '消息格式' }], secrets: [{ key: 'webhook_url', label: 'Webhook 地址', required: true, placeholder: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…' }] },
  { id: 'telegram', name: 'Telegram', help: '机器人令牌与要推送到的会话 ID。', plain: [{ key: 'chat_id', label: '会话 ID（Chat ID）', placeholder: '123456789' }], secrets: [{ key: 'bot_token', label: '机器人令牌（Bot Token）', required: true }] },
  { id: 'email', name: '邮件', help: '用 SMTP 发信；SMTP 服务器留空时按发件地址自动识别。', plain: [{ key: 'from_addr', label: '发件地址', placeholder: 'me@example.com' }, { key: 'to_addr', label: '收件地址（多个用逗号分隔）', placeholder: 'me@example.com' }, { key: 'smtp_server', label: 'SMTP 服务器（可不填）', placeholder: 'smtp.example.com' }, { key: 'smtp_port', label: 'SMTP 端口（可不填）', placeholder: '465' }], secrets: [{ key: 'password', label: '邮箱密码或授权码', required: true }] },
  { id: 'ntfy', name: 'ntfy', help: '主题（topic）必填；自建服务器填地址，留空用 https://ntfy.sh。', plain: [{ key: 'server_url', label: '服务器地址（可不填）', placeholder: 'https://ntfy.sh' }], secrets: [{ key: 'topic', label: '主题（topic）', required: true }, { key: 'token', label: '访问令牌（可不填）', required: false }] },
  { id: 'bark', name: 'Bark', help: 'Bark App 里复制的推送地址。', plain: [], secrets: [{ key: 'url', label: '推送地址', required: true, placeholder: 'https://api.day.app/…' }] },
  { id: 'slack', name: 'Slack', help: 'Incoming Webhook 地址。', plain: [], secrets: [{ key: 'webhook_url', label: 'Webhook 地址', required: true, placeholder: 'https://hooks.slack.com/services/…' }] },
  { id: 'webhook', name: '通用 Webhook', help: '推到你自己的接收地址（工作台自己的接收地址总会自动加上，不用填在这里）。', plain: [], secrets: [{ key: 'url', label: '接收地址', required: true, placeholder: 'https://example.com/hook' }] },
]
// 测试推送的结果用 TrendRadar 的渠道名（通用 Webhook 是 generic_webhook）
export const channelName = (id: string) => CHANNELS.find(c => c.id === (id === 'generic_webhook' ? 'webhook' : id))?.name ?? id

export const BRIEFING_STATUS: Record<NewsBriefing['status'], string> = { receiving: '接收中', complete: '已送达', pending: '生成中', failed: '失败' }
export const BRIEFING_SOURCE: Record<NewsBriefing['source'], string> = { trendradar: 'TrendRadar 推送', agent: '智能体撰写' }

// 工作台自己写的说明（不含外部内容）
const MESSAGES: Record<string, string> = {
  VERSION_CONFLICT: '设置已被别人修改：请点“重新加载”拿到最新的设置，再改一次。',
  NEWS_TAKEN: '另一个工作空间已经启用了早报（一个部署只能有一个），这里不能启用。',
  NEWS_DISABLED: '先启用早报并保存设置。',
  NO_CHANNELS: '还没有启用任何推送渠道：先在“推送渠道”里启用一个并保存。',
  NOT_AGENT_MODE: '只有方案 B（智能体写早报）能现在生成：先把早报方式改成方案 B 并保存。',
  BRIEFING_PENDING: '已经有一份早报正在写，等它完成或失败后再试。',
  NEWS_NOT_DEPLOYED: '这个部署没有安装 TrendRadar（新闻早报），不能推送。',
  RESULT_UNKNOWN: '推送结果未知：请稍后看看各渠道有没有收到，不要马上重发。',
  EXTERNAL_UNAVAILABLE: 'TrendRadar 暂时连不上，请稍后重试。',
  FORBIDDEN_ROLE: '只有 owner 能修改全局排除词。',
}
export function newsError(error: unknown): string {
  if (error instanceof ApiError && MESSAGES[error.code]) return MESSAGES[error.code]!
  return errorMessage(error)
}

function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  const active = () => user === auth.user?.id && scope === auth.workspaceId
  return { auth, user, header: { 'X-Workspace-Id': scope }, active }
}
export const newsKey = (user: string | undefined, scope: string, ...parts: unknown[]) => ['news', user, scope, ...parts]

export function useNewsToday(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: newsKey(c.user, scope, 'today'), enabled: computed(c.active),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/news/today', { params: { header: c.header }, signal })) })
}

export function useNewsBriefings(scope: string, limit = 20) {
  const c = context(scope)
  return useInfiniteQuery({ queryKey: newsKey(c.user, scope, 'briefings', limit), enabled: computed(c.active), initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => dataOf(await c.auth.api.GET('/api/v1/news/briefings', { params: { header: c.header, query: { limit, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
    getNextPageParam: p => p.next_cursor ?? undefined })
}

export function useNewsBriefing(scope: string, id: Ref<string | null>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => newsKey(c.user, scope, 'briefing', id.value)), enabled: computed(() => c.active() && !!id.value),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/news/briefings/{briefing_id}', { params: { header: c.header, path: { briefing_id: id.value! } }, signal })) })
}

export function useNewsHot(scope: string, platform: Ref<PlatformId | ''>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => newsKey(c.user, scope, 'hot', platform.value)), enabled: computed(c.active),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/news/hot', { params: { header: c.header, query: { limit: 100, ...(platform.value ? { platform: platform.value } : {}) } }, signal })) })
}

export function useNewsRss(scope: string, days: Ref<number>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => newsKey(c.user, scope, 'rss', days.value)), enabled: computed(c.active),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/news/rss', { params: { header: c.header, query: { days: days.value, limit: 100 } }, signal })) })
}

export type SearchParams = { q: string; days: number; include_rss: boolean }
export function useNewsSearch(scope: string, params: Ref<SearchParams | null>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => newsKey(c.user, scope, 'search', params.value)), enabled: computed(() => c.active() && !!params.value?.q),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/news/search', { params: { header: c.header, query: { ...params.value!, limit: 50 } }, signal })) })
}

export function useNewsFavorites(scope: string) {
  const c = context(scope)
  return useInfiniteQuery({ queryKey: newsKey(c.user, scope, 'feedback', 'favorite'), enabled: computed(c.active), initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => dataOf(await c.auth.api.GET('/api/v1/news/feedback', { params: { header: c.header, query: { kind: 'favorite', limit: 50, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
    getNextPageParam: p => p.next_cursor ?? undefined })
}

export function useNewsSettings(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: newsKey(c.user, scope, 'settings'), enabled: computed(() => c.active() && c.auth.canManage),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/news/settings', { params: { header: c.header }, signal })) })
}

export function useNewsStatus(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: newsKey(c.user, scope, 'status'), enabled: computed(c.active),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/news/status', { params: { header: c.header }, signal })) })
}

// 方案 B 选智能体实例：任务执行者的选项接口（成员都能看，只有实例 ID、名字与状态）
export function useNewsAgents(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: newsKey(c.user, scope, 'agents'), enabled: computed(() => c.active() && c.auth.canManage), staleTime: 60_000,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/tasks/assignees', { params: { header: c.header }, signal })).instances })
}

// 收藏要带的字段按后端上限截断（标题、来源都是第三方文字）
function feedbackBody(item: NewsItem, kind: 'favorite' | 'dismiss', excludeWord?: string): Schema['NewsFeedbackIn'] {
  const word = excludeWord?.trim().replace(/\s+/g, ' ')
  return {
    kind, url: item.url, title: (item.title.trim() || item.url).slice(0, 500), source_type: item.source_type,
    source_id: item.source_id.slice(0, 100), source_name: item.source_name.slice(0, 100), topic: (item.topic ?? '').slice(0, 30),
    ...(kind === 'dismiss' && word ? { exclude_word: word.slice(0, 60) } : {}),
  }
}

export function useNewsActions(scope: string) {
  const c = context(scope), cache = useQueryClient(), api = c.auth.api, header = c.header
  const refresh = () => cache.invalidateQueries({ predicate: q => q.queryKey[0] === 'news' && q.queryKey[1] === c.user && q.queryKey[2] === scope })
  return {
    refresh,
    async favorite(item: NewsItem) {
      try {
        if (item.favorite_id) await api.DELETE('/api/v1/news/feedback/{feedback_id}', { params: { header, path: { feedback_id: item.favorite_id } } })
        else dataOf(await api.POST('/api/v1/news/feedback', { params: { header }, body: feedbackBody(item, 'favorite') }))
      } finally { await refresh() }
    },
    async dismiss(item: NewsItem, excludeWord?: string) {
      try { return dataOf(await api.POST('/api/v1/news/feedback', { params: { header }, body: feedbackBody(item, 'dismiss', excludeWord) })) }
      finally { await refresh() }
    },
    async remove(feedbackId: string) {
      try { await api.DELETE('/api/v1/news/feedback/{feedback_id}', { params: { header, path: { feedback_id: feedbackId } } }) }
      finally { await refresh() }
    },
    async saveSettings(body: NewsSettingsBody) {
      const saved = dataOf(await api.PUT('/api/v1/news/settings', { params: { header }, body }))
      cache.setQueryData(newsKey(c.user, scope, 'settings'), saved)
      await refresh()
      return saved
    },
    async testPush() {
      return dataOf(await api.POST('/api/v1/news/settings/test-push', { params: { header } })).results
    },
    async generate() {
      try { return dataOf(await api.POST('/api/v1/news/briefings/generate', { params: { header } })) }
      finally { await refresh() }
    },
  }
}
