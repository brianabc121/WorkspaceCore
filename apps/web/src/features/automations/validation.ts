import { z } from 'zod'
import { ApiError, errorMessage, type Schema } from '../../services/api'

export const runLabels: Record<Schema['AutomationRunOut']['status'], string> = { queued: '已受理', running: '执行中', success: '成功', error: '失败', canceled: '已取消', unknown: '结果未知，正在确认' }
export function automationError(error: unknown) {
  const reasons: Record<string, string> = { disabled: '工作流已停用', not_configured: '尚未配置 n8n 连接', connection_unavailable: 'n8n 连接暂不可用，请管理员检查连接', workflow_inactive: '工作流尚未激活', webhook_not_found: '触发入口不存在或尚未激活', workflow_not_found: '找不到该工作流，请检查工作流 ID' }
  if (error instanceof ApiError) {
    if (error.code === 'AUTOMATION_UNAVAILABLE' || error.code === 'VALIDATION_FAILED' && error.details?.reason === 'workflow_not_found') return reasons[String(error.details?.reason)] ?? '工作流暂不可触发，请刷新状态。'
    if (error.code === 'INTEGRATION_UNAUTHORIZED') return 'n8n 凭据被拒绝，请管理员重新连接。'
    if (error.code === 'EXTERNAL_UNAVAILABLE') return 'n8n 暂不可用，请核对运行记录后再决定是否重试。'
    if (error.code === 'AUTOMATION_EXISTS') return '该工作流或触发入口已在白名单中。'
  }
  return errorMessage(error)
}
export function triggerParams(specs: Schema['ParamSpec'][], inputs: Record<string, string>): Schema['TriggerRequest']['params'] {
  const result: Record<string, string | number | boolean> = {}
  for (const spec of specs) {
    const value = inputs[spec.name] ?? ''
    if (!value) { if (spec.required) throw new Error(`${spec.label}为必填项。`); continue }
    if (spec.type === 'string') {
      if (spec.max_length && value.length > spec.max_length) throw new Error(`${spec.label}不能超过 ${spec.max_length} 字。`)
      if (spec.choices && !spec.choices.includes(value)) throw new Error(`${spec.label}请选择允许的选项。`)
      result[spec.name] = value
    } else if (spec.type === 'number') {
      if (!value.trim() || !Number.isFinite(Number(value))) throw new Error(`${spec.label}必须是有效数字。`)
      result[spec.name] = Number(value)
    } else { if (!['true', 'false'].includes(value)) throw new Error(`${spec.label}请选择是或否。`); result[spec.name] = value === 'true' }
  }
  return result
}
const param = z.object({ name: z.string().regex(/^[a-z][a-z0-9_]{0,31}$/), label: z.string().trim().min(1).max(64), type: z.enum(['string', 'number', 'boolean']), required: z.boolean().default(false), sensitive: z.boolean().default(false), max_length: z.number().int().min(1).max(2000).nullable().optional(), choices: z.array(z.string()).min(1).max(50).nullable().optional() }).strict().refine(p => p.type === 'string' || !p.max_length && !p.choices)
export const paramSpecs = z.array(param).max(20).refine(ps => new Set(ps.map(p => p.name)).size === ps.length)
