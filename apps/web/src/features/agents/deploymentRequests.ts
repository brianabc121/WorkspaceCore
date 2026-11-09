import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'

// 一次点击新建并部署（T-0182，改造契约第 5 节）：POST /agents/deployments 用 request_id 同时作为 Idempotency-Key；
// 连点、网络重试都用同一个 request_id，服务端只建一个实例和一个任务。结果未知（响应丢失、WRITE_UNKNOWN、页面刷新）时
// 先按 request_id 查收据（GET /agents/deployments/requests/{id}），不生成新的 request_id 重建。
//
// 浏览器只在 sessionStorage 里短暂记下 {request_id, instance_id?, user_id, workspace_id}：不存表单原文、不存任何密码或接入包；
// 读取时核对用户与工作空间，登出 / 换用户时清除（router.ts）。存储不可用（隐私模式等）时只是不能跨刷新找回。

export type DeploymentRecord = { request_id: string; instance_id?: string; user_id: string; workspace_id: string }
const KEY = 'myworkspace.agents.deployment-request'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export async function startAgentDeployment(scope: string, body: Schema['AgentDeploymentCreateIn']): Promise<Schema['AgentDeploymentCreateOut']> {
  const auth = useAuthStore()
  return dataOf(await auth.slowApi.POST('/api/v1/agents/deployments', { params: { header: { 'X-Workspace-Id': scope, 'Idempotency-Key': body.request_id } }, body }))
}

export async function getAgentDeploymentReceipt(scope: string, requestId: string): Promise<Schema['AgentDeploymentRequestOut']> {
  const auth = useAuthStore()
  return dataOf(await auth.api.GET('/api/v1/agents/deployments/requests/{request_id}', { params: { header: { 'X-Workspace-Id': scope }, path: { request_id: requestId } } }))
}

/** 只保存这四个字段（白名单），别的一律不写 */
export function rememberDeployment(record: DeploymentRecord) {
  const value: DeploymentRecord = { request_id: record.request_id, user_id: record.user_id, workspace_id: record.workspace_id, ...(record.instance_id ? { instance_id: record.instance_id } : {}) }
  try { sessionStorage.setItem(KEY, JSON.stringify(value)) } catch { /* 存储不可用：只是不能跨刷新找回 */ }
}

/** 当前用户、当前工作空间的未完成部署请求；别的用户留下的记录直接清掉 */
export function recallDeployment(userId: string | undefined, workspaceId: string | null): DeploymentRecord | null {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw || !userId || !workspaceId) return null
    const value = JSON.parse(raw) as Partial<DeploymentRecord>
    if (typeof value.request_id !== 'string' || !UUID.test(value.request_id) || typeof value.user_id !== 'string' || typeof value.workspace_id !== 'string') { sessionStorage.removeItem(KEY); return null }
    if (value.user_id !== userId) { sessionStorage.removeItem(KEY); return null }
    if (value.workspace_id !== workspaceId) return null
    return { request_id: value.request_id, user_id: value.user_id, workspace_id: value.workspace_id, ...(typeof value.instance_id === 'string' && value.instance_id ? { instance_id: value.instance_id } : {}) }
  } catch { return null }
}

/** 部署请求有了结论（成功、失败、被拒绝、找不到）或用户登出 / 换人时清除；给了 requestId 时只清这一条 */
export function forgetDeployment(requestId?: string) {
  try {
    if (requestId) {
      const raw = sessionStorage.getItem(KEY)
      if (raw && (JSON.parse(raw) as Partial<DeploymentRecord>).request_id !== requestId) return
    }
    sessionStorage.removeItem(KEY)
  } catch { /* 存储不可用：没有可清的 */ }
}
