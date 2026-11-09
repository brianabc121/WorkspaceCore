import { CHANNELS, type ChannelId, type NewsSettings, type NewsStatus } from './api'

// 设置 → 新闻早报的标签与“状态”文字（T-0195，方案 docs/v2/ai-admin-redesign 计划 B5）。只按接口给的字段如实描述：
// - 未部署、被另一个工作空间占用、连通未知、连不上、早报没启用各说各的，不把未知写成可用；
// - config_rev 是写入配置文件的次数，version 是保存时防止覆盖别人修改的版本号，两者不混用；
// - rendered_at 只说明配置文件写过，不说明 TrendRadar 已经按它运行过；渠道“已配置”也不等于推送送达。

/** 设置页的五个标签（当前标签写在地址里：?tab=） */
export const SETTINGS_TABS = [
  { key: 'basic', name: '基本与时间' },
  { key: 'sources', name: '新闻来源' },
  { key: 'topics', name: '兴趣主题' },
  { key: 'model', name: '模型与渠道' },
  { key: 'learned', name: '学习偏好' },
] as const
export type SettingsTab = typeof SETTINGS_TABS[number]['key']

export type Tone = 'green' | 'amber' | 'red' | 'muted' | 'blue'
export type State = { tone: Tone; label: string }
/** 保存、测试推送、生成之后给用户的一句话（problems：要改的地方） */
export type Feedback = { tone: 'good' | 'warn' | 'bad'; text: string; problems?: string[] }

/** 页面顶部摘要条：TrendRadar 能不能用 */
export function availability(settings: NewsSettings, status: NewsStatus | undefined, failed: boolean): State {
  if (!settings.deployed || status?.deployed === false) return { tone: 'muted', label: 'TrendRadar 未部署' }
  if (settings.taken_by_other) return { tone: 'red', label: '早报由另一个工作空间使用' }
  if (!status) return failed ? { tone: 'red', label: 'TrendRadar 状态读取失败' } : { tone: 'muted', label: '正在检查 TrendRadar…' }
  if (status.online === true) return { tone: 'green', label: 'TrendRadar 可用' }
  if (status.online === false) return { tone: 'red', label: 'TrendRadar 连不上' }
  if (!status.enabled) return { tone: 'amber', label: '早报未启用，不检查连通' }
  return { tone: 'amber', label: 'TrendRadar 连通状态未知' }
}

/** 右侧“当前运行状态”的“连通”一项 */
export function connectivity(settings: NewsSettings, status: NewsStatus | undefined, failed: boolean): State {
  if (!settings.deployed || status?.deployed === false) return { tone: 'muted', label: '—（未部署）' }
  if (!status) return failed ? { tone: 'red', label: '读取失败' } : { tone: 'muted', label: '正在检查…' }
  if (status.online === true) return { tone: 'green', label: '在线' }
  if (status.online === false) return { tone: 'red', label: '连不上' }
  if (!status.enabled) return { tone: 'muted', label: '不检查（早报未启用）' }
  return { tone: 'amber', label: '未知' }
}

/** 配置文件：写入失败 / 还没写过 / 最近一次写成功的序号 */
export function configFile(settings: NewsSettings): State {
  if (settings.render_error) return { tone: 'red', label: '写入失败' }
  if (!settings.config_rev) return { tone: 'muted', label: '还没有写入' }
  return { tone: 'green', label: `已写入 #${settings.config_rev}` }
}

/** 已保存的渠道：启用了的，凭证是否齐（只看“已设置”标记，不读原值） */
export function savedChannels(settings: NewsSettings) {
  const enabled = CHANNELS.filter(def => settings.channels[def.id]?.enabled)
  return {
    enabled: enabled.map(def => {
      const set = settings.channels[def.id]?.secrets_set ?? []
      const missing = def.secrets.filter(f => f.required && !set.includes(f.key))
      const present = def.secrets.filter(f => set.includes(f.key))
      return {
        id: def.id as ChannelId, name: def.name,
        state: missing.length
          ? { tone: 'red', label: `已启用，但缺少${missing.map(f => f.label).join('、')}` } as State
          : { tone: 'green', label: `已配置（${present.map(f => f.label).join('、') || '凭证'}已设置，不回显）` } as State,
      }
    }),
    disabled: CHANNELS.length - enabled.length,
  }
}

export const MODE_NAMES: Record<NewsSettings['briefing_mode'], string> = { trendradar: 'TrendRadar 汇总', agent: '智能体写早报' }
export const INSTANCE_STATUS: Record<string, string> = { online: '在线', offline: '离线', pending: '等待接入' }
