import { computed } from 'vue'
import { useInfiniteQuery, useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'

// 实例的主模型（T-0206 / T-0207，契约第 3 节）：连接 + 模型是“目标”，实例确认后才是“已应用”。
// 查询键以 'agents' 开头（写操作后 useAgentAction 一起刷新），带 user + workspace，切换身份时不会读到上一个身份的缓存。

export type Binding = Schema['AgentModelBindingOut']
export type BindingView = Schema['ModelBindingView']
export type ApplyState = NonNullable<BindingView['apply_state']>
export type ConnectionOption = Schema['PickerConnectionOut']
export type ModelChoice = { connection_id: string; model_id: string }

function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  const active = () => user === auth.user?.id && scope === auth.workspaceId
  return { auth, user, header: { 'X-Workspace-Id': scope }, enabled: computed(() => active() && auth.isOwner) }
}

export const bindingKey = (user: string | undefined, scope: string, instanceId: string) => ['agents', user, scope, 'model-binding', instanceId] as const

/** 实例的绑定；在途（待应用 / 应用中）时每 5 秒刷新一次，离开页面（组件卸载）即停止 */
export function useModelBinding(scope: string, instanceId: () => string | null) {
  const c = context(scope)
  return useQuery<BindingView>({
    queryKey: computed(() => bindingKey(c.user, scope, instanceId() ?? '')),
    enabled: computed(() => c.enabled.value && !!instanceId()),
    retry: false,
    refetchInterval: query => inFlight((query.state.data as BindingView | undefined)?.apply_state) ? 5000 : false,
    queryFn: async ({ signal }): Promise<BindingView> => dataOf(await c.auth.api.GET('/api/v1/agents/instances/{instance_id}/model-binding', { params: { header: c.header, path: { instance_id: instanceId()! } }, signal })),
  })
}

/** 可选的连接：启用、配置完整、协议已开放。instanceId 为 null 表示新建实例（不按授权筛选，选择即授权） */
export function useConnectionOptions(scope: string, instanceId: () => string | null, enabled: () => boolean = () => true) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => ['agents', c.user, scope, 'model-options', instanceId()] as const),
    enabled: computed(() => c.enabled.value && enabled()),
    queryFn: async ({ signal }) => {
      const id = instanceId()
      return dataOf(await c.auth.api.GET('/api/v1/agents/model-connection-options', { params: { header: c.header, query: id ? { instance_id: id } : {} }, signal }))
    },
  })
}

/** 一条连接的模型集合（明确的模型 ID，每页 100，可以继续加载） */
export function useConnectionModels(scope: string, connectionId: () => string | null) {
  const c = context(scope)
  return useInfiniteQuery({
    queryKey: computed(() => ['agents', c.user, scope, 'connection-models', connectionId()] as const),
    enabled: computed(() => c.enabled.value && !!connectionId()),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => dataOf(await c.auth.api.GET('/api/v1/model-connections/{connection_id}/models', { params: { header: c.header, path: { connection_id: connectionId()! }, query: { limit: 100, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
    getNextPageParam: p => p.next_cursor ?? undefined,
  })
}

export const inFlight = (state: ApplyState | null | undefined) => state === 'pending' || state === 'applying'

/** 应用状态的文字与色调：保存不等于已生效，失败、未知、待手动都分开说 */
export function applyStatus(state: ApplyState | null | undefined): { label: string; tone: 'green' | 'amber' | 'red' | 'muted' | 'blue'; hint: string } {
  switch (state) {
    case 'applied': return { label: '已应用', tone: 'green', hint: '实例已确认在用这个连接与模型。' }
    case 'pending': return { label: '待应用', tone: 'blue', hint: '已保存，等实例取走新配置（每分钟一次）。' }
    case 'applying': return { label: '应用中', tone: 'blue', hint: '实例已取走新配置，正在校验并写入。' }
    case 'failed': return { label: '应用失败', tone: 'red', hint: '实例仍按上一次已应用的配置运行；处理原因后点“重新应用”。' }
    case 'unknown': return { label: '状态待确认', tone: 'amber', hint: '10 分钟内没有收到实例的回报：可能离线，或这个实例的接入包还没有配置应用主模型的定时任务。' }
    case 'manual_required': return { label: '待手动应用', tone: 'amber', hint: '实例不能自动改配置，请按接入包 CHECKLIST.md 手动应用后再点“重新应用”。' }
    case 'legacy_active': return { label: '旧配置', tone: 'muted', hint: '仍按旧接入包里的模型配置运行，工作台不猜它用的是哪条连接；选择主模型后自动迁移。' }
    default: return { label: '未设置', tone: 'muted', hint: '还没有设置主模型。' }
  }
}

/** 实例回报的失败原因（固定代码，不含远端原始输出） */
export function reasonText(code: string | null | undefined): string {
  if (!code) return ''
  const known: Record<string, string> = {
    invalid_config: 'OpenClaw 校验配置没有通过',
    credential_unavailable: 'OpenClaw 取不到这条连接的 Key（检查授权与启用状态）',
    validate_failed: 'OpenClaw 校验命令失败',
    patch_failed: '写入配置失败',
    reload_failed: '网关没有响应 secrets reload（可能没在运行）',
    config_unreadable: '读不了实例上的 openclaw.json',
    no_report: '没有收到实例的回报',
    superseded: '已被更新的目标取代',
    apply_failed: '应用失败',
    manual_required: '需要手动处理',
  }
  return known[code] ?? code
}

export function bindingError(error: unknown): string {
  if (!(error instanceof ApiError)) return '请求失败，请稍后重试。'
  const messages: Record<string, string> = {
    MODEL_CONNECTION_DISABLED: '这条连接已停用：到中转站管理里启用后再选。',
    MODEL_CONNECTION_NOT_READY: '这条连接还没有配置完整（协议、地址、Key、模型）。',
    MODEL_PROTOCOL_UNSUPPORTED: '这条连接的协议还没有开放。',
    MODEL_NOT_ALLOWED: error.status === 422 ? '这个模型不在连接的模型集合里。' : '这条连接还没有授权给这个智能体：先到中转站管理的“智能体授权”里授权。',
    CREDENTIAL_UNREADABLE: '这条连接的 Key 无法解密，请到中转站管理里重新填写。',
    VERSION_CONFLICT: '主模型已被其他人修改，请刷新后再操作。',
    NOT_FOUND: '连接或智能体不存在，或你已无权访问。',
    FORBIDDEN: '只有 owner 可以设置智能体的主模型。',
  }
  return messages[error.code] ?? (error.status === 0 ? '网络连接失败，请检查连接后重试。' : '请求失败，请稍后重试。')
}
