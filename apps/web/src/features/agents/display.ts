import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { IM_STATUS } from './queries'
import type { Tone } from './servers'

// OpenClaw 智能体页（T-0182）的显示口径：消息连接、部署任务、最近心跳分别表达（改造规范第 3.3 节）。
// 消息在线以 OpenIM 上下线为准；部署任务看最近一次部署 / 卸载任务与部署记录；都不推断“容器运行中 / 已停止”。
export type OverviewRow = Schema['AgentOverviewRow']
type InstanceStatus = Schema['AgentInstanceOut']['status']

export const IM_TONE: Record<InstanceStatus, Tone> = { pending: 'amber', online: 'green', offline: 'red', disabled: 'muted' }
export const imStatus = (status: InstanceStatus) => ({ label: IM_STATUS[status], tone: IM_TONE[status] })

export function deployTask(job: Schema['AgentSshJobOut'] | null, deployment: Schema['AgentDeploymentOut'] | null): { label: string; tone: Tone } {
  if (job && job.kind !== 'prepare') {
    const kind = job.kind === 'uninstall' ? '卸载' : '部署'
    if (job.status === 'running') return { label: `${kind}中`, tone: 'blue' }
    if (job.status === 'failed') return { label: `${kind}失败`, tone: 'red' }
    if (job.status === 'interrupted') return { label: `${kind}中断`, tone: 'red' }
    if (job.kind === 'uninstall') return { label: deployment?.runtime_installed === false ? '已卸载（保留数据）' : '已卸载', tone: 'amber' }
    return { label: '部署成功', tone: 'green' }
  }
  if (deployment) {
    if (deployment.runtime_installed === false) return { label: '已卸载（保留数据）', tone: 'amber' }
    return deployment.installed_at ? { label: '已部署', tone: 'green' } : { label: '有部署记录', tone: 'muted' }
  }
  return { label: '手动接入', tone: 'muted' }
}

/** 最近心跳：多久以前（悬停看具体时间）；从未连接写 — */
export function ago(value: string | null, now = Date.now()): string {
  if (!value) return '—'
  const time = new Date(value).getTime()
  if (Number.isNaN(time)) return '—'
  const seconds = Math.max(0, Math.round((now - time) / 1000))
  if (seconds < 60) return `${seconds} 秒前`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`
  return formatTime(value)
}
