import { ApiError, type Schema } from '../../services/api'
import { agentError } from './queries'

// 密钥库页的显示与错误文字（T-0196，规范第 10 节）。not_found 不区分“未授权”与“不存在”（服务端就不区分）。
export const ACCESS_RESULT = { ok: '成功', not_found: '未授权或不存在', rate_limited: '过于频繁' } as const
export const ACCESS_TONE: Record<Schema['SecretAccessOut']['result'], string> = { ok: 'green', not_found: 'muted', rate_limited: 'amber' }

/** 授权给了哪些智能体（同一实例用两个引用名时只算一次） */
export function grantedInstances(secret: Pick<Schema['AgentSecretOut'], 'grants'>) {
  return [...new Map(secret.grants.map(g => [g.instance_id, g.instance_name])).entries()].map(([id, name]) => ({ id, name }))
}

export const isConflict = (error: unknown) => error instanceof ApiError && error.code === 'VERSION_CONFLICT'
export const isGone = (error: unknown) => error instanceof ApiError && [403, 404, 422].includes(error.status)

export function secretError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'WRITE_UNKNOWN') return '连接中断，结果未知：操作可能已经生效。请刷新后核对密钥的当前版本与信息，再决定是否重试。'
    if (isConflict(error)) return '密钥已被其他操作修改，这次没有保存。已重新读取最新信息，请核对后再操作。'
    if (error.status === 404) return '这个密钥不存在，或你已无权访问。'
  }
  return agentError(error)
}
