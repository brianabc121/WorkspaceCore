// 项目群状态的说明文字与可做的操作（T-0110）
import type { Schema } from '../../services/api'

const reasons: Record<string, string> = {
  unavailable: 'OpenIM 暂时不可用',
  timeout_unknown: '上次建群的结果未知',
  not_configured: '没有部署 OpenIM',
  owner_missing: '找不到可以当群主的管理员',
}
export function groupState(group: Schema['ProjectImGroup']): { text: string; short: string; action: string | null } {
  if (group.status === 'ready') return { text: '项目群已建好，群成员跟随项目的智能体成员。', short: '项目群已建好', action: null }
  if (group.status === 'pending') {
    const reason = group.error ? `：${reasons[group.error] ?? '出错了'}` : ''
    return { text: `项目群还没建好${reason}，会每分钟自动重试。`, short: '项目群还没建好', action: '立即重试' }
  }
  return { text: '还没有项目群。建好后，群主是你，成员是你和已加入的智能体。', short: '没有项目群', action: '建项目群' }
}
