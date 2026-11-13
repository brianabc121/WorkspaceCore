import { computed, type Ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, errorMessage, type Schema } from '../../services/api'
import { formatTime } from '../../features/time'

// 大模型会话桥的页面接口（services/api/app/modules/llm_bridge/api.py，都在 /api/v1/llm_bridge 下）。
// 查看要 admin，修改要 owner（后端同样校验）。登录、设置、环境、会话都由工作台转给会话桥。
// 令牌、API 密钥只在提交时出现在请求体里：不进查询缓存、不写日志，提交成功后输入框清空。
// 查询键都以 'llm_bridge' 开头：['llm_bridge', userId, workspaceId, ...]。
export type Bridge = Schema['BridgeOut']
export type BridgeStatus = Schema['BridgeStatusOut']
export type Account = Schema['AccountOut']
export type BridgeSettings = Schema['BridgeSettings']
export type SettingsPatch = Schema['BridgeSettingsPatch']
export type LoginBody = Schema['LoginIn']
export type LoginMethod = LoginBody['method']
export type LoginState = Schema['LoginOut']
export type Environment = Schema['EnvironmentOut']
export type EnvironmentBody = Schema['EnvironmentIn']
export type Grant = Schema['GrantOut']
export type GrantBody = Schema['GrantIn']
export type Platform = GrantBody['platforms'][number]
export type BridgeSession = Schema['SessionList']['sessions'][number]
export type Turn = Schema['TurnOut']
export type Slot = 'openai' | 'claude' | 'workbuddy'

export const PLATFORMS: readonly { id: Platform; name: string }[] = [
  { id: 'chatgpt', name: 'ChatGPT' }, { id: 'codex', name: 'Codex' }, { id: 'claude', name: 'Claude' }, { id: 'workbuddy', name: 'WorkBuddy' },
]
export const platformName = (id: string) => PLATFORMS.find(p => p.id === id)?.name ?? id
export const ACCOUNT_STATE: Record<string, { text: string; tone: string }> = {
  logged_in: { text: '已登录', tone: 'tone-green' },
  logged_out: { text: '未登录', tone: 'tone-muted' },
  mismatch: { text: '账号不一致', tone: 'tone-red' },
  unavailable: { text: '不可用', tone: 'tone-amber' },
}
export const METHODS: Record<LoginMethod, { name: string; help: string }> = {
  device_code: { name: '设备码登录', help: '页面给出网址和验证码，你在自己的浏览器里打开网址、输入验证码完成登录。' },
  api_key: { name: 'API 密钥', help: '密钥保存在会话桥的数据卷里，不保存在工作台。' },
  oauth_token: { name: '订阅令牌', help: '用官方程序生成的长期令牌登录订阅账号；令牌保存在会话桥的数据卷里，不保存在工作台。' },
  existing: { name: '容器里已有的登录', help: '使用已经在容器里完成的登录。' },
}
export const methodName = (id: string | null) => (id ? METHODS[id as LoginMethod]?.name ?? id : '—')
export const SANDBOX: Record<EnvironmentBody['sandbox'], string> = { read_only: '只读', workspace_write: '可写工作区', exec: '可执行命令' }
export const MODES: Record<string, string> = { dev: '开发', cloud: '云端', chat: '对话' }
const STATUS: Record<string, string> = { active: '进行中', archived: '已归档', queued: '排队中', running: '进行中', completed: '已完成', done: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断', timeout: '超时', pending: '等待中' }
export const statusName = (id: string) => STATUS[id] ?? id
const ORIGIN: Record<string, string> = { bridge: '经会话桥新建', external: '官方程序里已有' }
export const originName = (id: string) => ORIGIN[id] ?? id
/** 会话桥给的时间是秒（浮点数） */
export const seconds = (value: number | null | undefined) => (value ? formatTime(new Date(value * 1000).toISOString()) : '—')

// 工作台自己写的说明；会话桥返回的错误（CONFLICT 等）带的是会话桥写的中文说明，按纯文字显示
const MESSAGES: Record<string, string> = {
  EXTERNAL_UNAVAILABLE: '会话桥暂时连不上：请确认会话桥容器在运行、登记的地址能从工作台访问，稍后重试。',
  BRIDGE_AUTH_FAILED: '会话桥不接受登记的令牌：请在“修改”里换成会话桥现在用的管理令牌。',
  NAME_TAKEN: '已经有同名的会话桥，换一个名字。',
  FORBIDDEN_ROLE: '只有 owner 能修改大模型会话桥。',
  MODULE_DISABLED: '功能已停用：大模型会话桥在当前工作空间没有启用。',
}
const BRIDGE_CODES = ['CONFLICT', 'NOT_SUPPORTED', 'NOT_LOGGED_IN', 'ACCOUNT_MISMATCH', 'UNAVAILABLE', 'RATE_LIMITED']
export function bridgeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (MESSAGES[error.code]) return MESSAGES[error.code]!
    if (BRIDGE_CODES.includes(error.code) && error.message) return error.message
  }
  return errorMessage(error)
}
/** 会话桥连不上或令牌不对（503）：页面在原地显示说明，不当成页面出错 */
export const isBridgeDown = (error: unknown) => error instanceof ApiError && ['EXTERNAL_UNAVAILABLE', 'BRIDGE_AUTH_FAILED'].includes(error.code)

const key = (user: string | undefined, scope: string, ...parts: unknown[]) => ['llm_bridge', user, scope, ...parts]

/** 一个工作空间的会话桥接口：查询用 vue-query，写操作是普通的 async 函数（调用方自己显示结果） */
export function useLlmBridge(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id, cache = useQueryClient()
  const header = { 'X-Workspace-Id': scope }
  const active = () => user === auth.user?.id && scope === auth.workspaceId && auth.canManage
  const invalidate = (...parts: unknown[]) => cache.invalidateQueries({ queryKey: key(user, scope, ...parts) })
  const at = (bridge_id: string) => ({ header, path: { bridge_id } })
  const slotAt = (bridge_id: string, slot: Slot) => ({ header, path: { bridge_id, slot } })

  return {
    canWrite: computed(() => auth.isOwner),
    invalidate,
    useBridges: () => useQuery({ queryKey: key(user, scope, 'bridges'), enabled: computed(active),
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/bridges', { params: { header }, signal })).bridges }),
    useStatus: (id: Ref<string | null>) => useQuery({ queryKey: computed(() => key(user, scope, 'bridge', id.value, 'status')), enabled: computed(() => active() && !!id.value),
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/bridges/{bridge_id}/status', { params: at(id.value!), signal })) }),
    useEnvironments: (id: Ref<string>) => useQuery({ queryKey: computed(() => key(user, scope, 'bridge', id.value, 'environments')), enabled: computed(active),
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/bridges/{bridge_id}/environments', { params: at(id.value), signal })).environments }),
    useSessions: (id: Ref<string>, offset: Ref<number>, limit: number) => useQuery({ queryKey: computed(() => key(user, scope, 'bridge', id.value, 'sessions', offset.value, limit)), enabled: computed(active),
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/bridges/{bridge_id}/sessions', { params: { ...at(id.value), query: { limit, offset: offset.value } }, signal })) }),
    useTurns: (id: Ref<string>, session: Ref<string | null>) => useQuery({ queryKey: computed(() => key(user, scope, 'bridge', id.value, 'turns', session.value)), enabled: computed(() => active() && !!session.value),
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/bridges/{bridge_id}/sessions/{session_id}/turns', { params: { header, path: { bridge_id: id.value, session_id: session.value! } }, signal })).turns }),
    useGrants: () => useQuery({ queryKey: key(user, scope, 'grants'), enabled: computed(active),
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/grants', { params: { header }, signal })).grants }),

    async createBridge(body: Schema['BridgeIn']) {
      const out = dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges', { params: { header }, body }))
      await invalidate('bridges'); return out
    },
    async updateBridge(id: string, body: Schema['BridgePatch']) {
      const out = dataOf(await auth.api.PATCH('/api/v1/llm_bridge/bridges/{bridge_id}', { params: at(id), body }))
      await Promise.all([invalidate('bridges'), invalidate('bridge', id)]); return out
    },
    async deleteBridge(id: string) {
      await auth.api.DELETE('/api/v1/llm_bridge/bridges/{bridge_id}', { params: at(id) })
      cache.removeQueries({ queryKey: key(user, scope, 'bridge', id) })
      await invalidate('bridges')
    },
    async saveSettings(id: string, body: SettingsPatch) {
      const out = dataOf(await auth.api.PUT('/api/v1/llm_bridge/bridges/{bridge_id}/settings', { params: at(id), body }))
      await invalidate('bridge', id, 'status'); return out
    },
    // 同步官方程序里的会话：会话桥要去读官方程序的记录，比普通请求慢
    async syncSessions(id: string, limit: number) {
      const out = dataOf(await auth.slowApi.GET('/api/v1/llm_bridge/bridges/{bridge_id}/sessions', { params: { ...at(id), query: { limit, offset: 0, sync: true } } }))
      await invalidate('bridge', id, 'sessions'); return out
    },
    async login(id: string, slot: Slot, body: LoginBody) {
      return dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/accounts/{slot}/login', { params: slotAt(id, slot), body }))
    },
    async loginState(id: string, slot: Slot) {
      return dataOf(await auth.api.GET('/api/v1/llm_bridge/bridges/{bridge_id}/accounts/{slot}/login', { params: slotAt(id, slot) }))
    },
    async refreshAccount(id: string, slot: Slot) {
      const out = dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/accounts/{slot}/refresh', { params: slotAt(id, slot) }))
      await invalidate('bridge', id, 'status'); return out
    },
    async logout(id: string, slot: Slot) {
      await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/accounts/{slot}/logout', { params: slotAt(id, slot) })
      await invalidate('bridge', id, 'status')
    },
    async resetAccount(id: string, slot: Slot) {
      const out = dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/accounts/{slot}/reset', { params: slotAt(id, slot) }))
      await invalidate('bridge', id); return out
    },
    async saveEnvironment(id: string, envId: string | null, body: EnvironmentBody) {
      const out = envId
        ? dataOf(await auth.api.PUT('/api/v1/llm_bridge/bridges/{bridge_id}/environments/{env_id}', { params: { header, path: { bridge_id: id, env_id: envId } }, body }))
        : dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/environments', { params: at(id), body }))
      await invalidate('bridge', id, 'environments'); return out
    },
    async deleteEnvironment(id: string, envId: string) {
      await auth.api.DELETE('/api/v1/llm_bridge/bridges/{bridge_id}/environments/{env_id}', { params: { header, path: { bridge_id: id, env_id: envId } } })
      await invalidate('bridge', id, 'environments')
    },
    async setGrant(instanceId: string, body: GrantBody) {
      const out = dataOf(await auth.api.PUT('/api/v1/llm_bridge/grants/{instance_id}', { params: { header, path: { instance_id: instanceId } }, body }))
      cache.setQueryData<Grant[]>(key(user, scope, 'grants'), old => old?.map(g => (g.instance_id === out.instance_id ? out : g)))
      return out
    },
  }
}
export type LlmBridgeApi = ReturnType<typeof useLlmBridge>
