import { computed, type Ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, errorMessage, type Schema } from '../../services/api'
import { formatTime } from '../../features/time'

// 大模型客户端管理的页面接口（services/api/app/modules/llm_bridge/api.py，都在 /api/v1/llm_bridge 下）。
// 页面上的“MCP 管理器”就是一座登记在工作台里的会话桥（接口里叫 bridge）：一个单独部署的容器，
// 控制勾选的平台、单独维护一套账号。
// 查看要 admin，修改要 owner（后端同样校验）。登录、设置、环境、会话都由工作台转给会话桥。
// 令牌、API 密钥只在提交时出现在请求体里：不进查询缓存、不写日志，提交成功后输入框清空。
// 查询键都以 'llm_bridge' 开头：['llm_bridge', userId, workspaceId, ...]。
export type Bridge = Schema['LlmBridgeOut']
export type BridgeStatus = Schema['LlmBridgeStatusOut']
export type Account = Schema['LlmBridgeAccountOut']
export type BridgeSettings = Schema['LlmBridgeSettings']
export type SettingsPatch = Schema['LlmBridgeSettingsPatch']
export type LoginBody = Schema['LlmBridgeLoginIn']
export type LoginMethod = LoginBody['method']
export type LoginState = Schema['LlmBridgeLoginOut']
export type Environment = Schema['LlmBridgeEnvironmentOut']
export type EnvironmentBody = Schema['LlmBridgeEnvironmentIn']
export type Grant = Schema['LlmBridgeGrantOut']
export type GrantBody = Schema['LlmBridgeGrantIn']
export type Platform = GrantBody['platforms'][number]
export type BridgeSession = Schema['LlmBridgeSessionList']['sessions'][number]
export type Turn = Schema['LlmBridgeTurnOut']
export type Slot = 'openai' | 'claude' | 'workbuddy'
export type DeployJob = Schema['LlmBridgeDeployJobOut']
export type DeployBody = Schema['LlmBridgeDeployIn']

// 页面上一键部署 MCP 管理器（docs/v2/LLM_BRIDGE_PLAN.md 第 20 节）：任务的种类、状态和每一步的名字
export const DEPLOY_KIND: Record<string, string> = { deploy: '部署', redeploy: '重新部署', uninstall: '卸载' }
export const DEPLOY_STATUS: Record<string, string> = { running: '进行中', succeeded: '成功', failed: '失败', interrupted: '已中断' }
export const DEPLOY_STEP_STATUS: Record<string, string> = { pending: '等待', running: '进行中', done: '完成', failed: '失败' }
export const DEPLOY_STEP: Record<string, string> = {
  connect: '登录服务器', inspect: '检查服务器', upload: '上传构建文件', build: '构建镜像（第一次要几分钟）', start: '启动容器',
  verify: '工作台连接管理器', register: '登记到工作台', stop: '停止并删除容器', remove: '删除部署目录', unregister: '从工作台去掉登记',
}
export const newDeployRequestId = () => (typeof crypto.randomUUID === 'function' ? `web-${crypto.randomUUID()}` : `web-${Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('')}`)

export const PLATFORMS: readonly { id: Platform; name: string }[] = [
  { id: 'chatgpt', name: 'ChatGPT' }, { id: 'codex', name: 'Codex' }, { id: 'claude', name: 'Claude' }, { id: 'workbuddy', name: 'WorkBuddy' },
]
export const platformName = (id: string) => PLATFORMS.find(p => p.id === id)?.name ?? id
/** 平台用哪个账号登录：ChatGPT 聊天和 Codex 用的是同一个 ChatGPT 账号 */
export const PLATFORM_SLOT: Record<Platform, Slot> = { chatgpt: 'openai', codex: 'openai', claude: 'claude', workbuddy: 'workbuddy' }
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
const STATUS: Record<string, string> = { active: '进行中', archived: '已归档', queued: '排队中', running: '进行中', completed: '已完成', done: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断', timeout: '超时', pending: '等待中', unknown: '结果未知' }
export const statusName = (id: string) => STATUS[id] ?? id
const ORIGIN: Record<string, string> = { bridge: '经 MCP 管理器新建', external: '官方程序里已有' }
export const originName = (id: string) => ORIGIN[id] ?? id
/** 会话桥给的时间是秒（浮点数） */
export const seconds = (value: number | null | undefined) => (value ? formatTime(new Date(value * 1000).toISOString()) : '—')

// 工作台自己写的说明；会话桥返回的错误（CONFLICT 等）带的是会话桥写的中文说明，按纯文字显示
const MESSAGES: Record<string, string> = {
  EXTERNAL_UNAVAILABLE: 'MCP 管理器暂时连不上：请确认它的容器在运行、填的地址能从工作台访问，稍后重试。',
  BRIDGE_AUTH_FAILED: 'MCP 管理器不接受这枚令牌：请换成它现在用的管理令牌（在服务器上用 make llm-bridge-token 查看）。',
  BRIDGE_OUTDATED: '这个 MCP 管理器的版本太旧，不支持选择控制的平台：请先在服务器上升级它（再运行一次部署命令），然后重新添加。',
  NAME_TAKEN: '已经有同名的 MCP 管理器，换一个名字。',
  FORBIDDEN_ROLE: '只有 owner 能修改大模型客户端。',
  MODULE_DISABLED: '功能已停用：大模型客户端在当前工作空间没有启用。',
  // 页面自己给的：会话桥因为还没确认风险拒绝了 Claude 的订阅登录（403）
  LB_ACK_REQUIRED: 'Claude 用订阅登录前，要先确认了解合规风险：请勾选下面的风险确认后再试。',
}
const BRIDGE_CODES = ['CONFLICT', 'NOT_SUPPORTED', 'NOT_LOGGED_IN', 'ACCOUNT_MISMATCH', 'UNAVAILABLE', 'RATE_LIMITED']
export function bridgeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (MESSAGES[error.code]) return MESSAGES[error.code]!
    if (BRIDGE_CODES.includes(error.code) && error.message) return error.message
  }
  return errorMessage(error)
}
/** 一键部署的错误：这些是工作台自己写的中文说明（服务器现在为什么不能部署、有任务在进行等），直接显示 */
export function deployError(error: unknown): string {
  if (error instanceof ApiError && [409, 503].includes(error.status) && error.message && !MESSAGES[error.code]) return error.message
  return bridgeError(error)
}
/** MCP 管理器连不上或令牌不对（503）：页面在原地显示说明，不当成页面出错 */
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
    // 一键部署：有任务在跑时每 2 秒读一次进度
    useDeployments: () => useQuery({ queryKey: key(user, scope, 'deployments'), enabled: computed(active),
      refetchInterval: (query: { state: { data?: Schema['LlmBridgeDeployJobList'] } }) => (query.state.data?.jobs.some(j => j.status === 'running') ? 2000 : false),
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/deployments', { params: { header }, signal })) }),
    useDeployment: (id: Ref<string | null>) => useQuery({ queryKey: computed(() => key(user, scope, 'deployment', id.value)), enabled: computed(() => active() && !!id.value),
      refetchInterval: (query: { state: { data?: DeployJob } }) => (query.state.data?.status === 'running' ? 2000 : false),
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/deployments/{job_id}', { params: { header, path: { job_id: id.value! } }, signal })) }),
    useGrants: () => useQuery({ queryKey: key(user, scope, 'grants'), enabled: computed(active),
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/grants', { params: { header }, signal })).grants }),

    async createBridge(body: Schema['LlmBridgeIn']) {
      const out = dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges', { params: { header }, body }))
      await invalidate('bridges'); return out
    },
    async updateBridge(id: string, body: Schema['LlmBridgePatch']) {
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
    async deploy(body: DeployBody) {
      const out = dataOf(await auth.api.POST('/api/v1/llm_bridge/deployments', { params: { header }, body }))
      await invalidate('deployments'); return out
    },
    async redeploy(id: string, request_id: string) {
      const out = dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/redeploy', { params: at(id), body: { request_id } }))
      await invalidate('deployments'); return out
    },
    async uninstall(id: string, request_id: string, remove_data: boolean) {
      const out = dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/uninstall', { params: at(id), body: { request_id, remove_data } }))
      await invalidate('deployments'); return out
    },
    async setGrant(instanceId: string, body: GrantBody) {
      const out = dataOf(await auth.api.PUT('/api/v1/llm_bridge/grants/{instance_id}', { params: { header, path: { instance_id: instanceId } }, body }))
      cache.setQueryData<Grant[]>(key(user, scope, 'grants'), old => old?.map(g => (g.instance_id === out.instance_id ? out : g)))
      return out
    },
  }
}
export type LlmBridgeApi = ReturnType<typeof useLlmBridge>
