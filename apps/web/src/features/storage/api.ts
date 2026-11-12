import { computed } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, errorMessage, type Schema } from '../../services/api'

// 图片存储设置（T-0223）：知识库图片与常驻环境快照存在哪里。owner 修改，admin 只读，按工作空间分开。
// 密钥只写不读：接口只说“是否已设置”；页面输入的密钥只在组件内存里，提交后清空，不进查询缓存或浏览器存储。
export type StorageSettings = Schema['StorageSettingsOut']
export type StorageSettingsBody = Schema['StorageSettingsIn']
export type StorageName = Schema['StorageSettingsOut']['primary']
export type ReplicaName = Schema['StorageSettingsOut']['replica']
export type StorageCheck = Schema['StorageCheck']

export const STORAGE_NAMES: Record<StorageName, string> = { local: '本机', r2: 'Cloudflare R2', oss: '阿里云 OSS' }

// 地址只由这些值拼出（与后端相同的规则）：页面预览给人核对，真正的地址由后端生成
export const r2Host = (account: string, jurisdiction: string) =>
  `https://${account || '<账户 ID>'}${jurisdiction ? `.${jurisdiction}` : ''}.r2.cloudflarestorage.com`
export const ossHost = (bucket: string, region: string) =>
  `https://${bucket || '<桶名>'}.s3.oss-${region.replace(/^oss-/, '') || '<地域>'}.aliyuncs.com`

export const storageKey = (user: string | undefined, scope: string) => ['storage-settings', user, scope]

export function useStorageSettings(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id, header = { 'X-Workspace-Id': scope }
  const active = () => user === auth.user?.id && scope === auth.workspaceId && auth.canManage
  const cache = useQueryClient()
  const query = useQuery({
    queryKey: storageKey(user, scope), enabled: computed(active),
    queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/settings/storage', { params: { header }, signal })),
  })
  async function save(body: StorageSettingsBody): Promise<StorageSettings> {
    // 保存前后端要对主存储与副本各写、读、删一个探测对象，可能比普通请求慢：用 60 秒超时的客户端
    const saved = dataOf(await auth.slowApi.PUT('/api/v1/settings/storage', { params: { header }, body }))
    // 自检结果只在保存的响应里有；缓存里放不带 checks 的设置，运维页的存储状态也要重新读
    cache.setQueryData(storageKey(user, scope), { ...saved, checks: null })
    void cache.invalidateQueries({ queryKey: ['operations', user, scope, 'storage'] })
    return saved
  }
  return { query, save }
}

/** 保存前自检没通过（422 STORAGE_CHECK_FAILED）：返回各存储的结果，设置没有保存 */
export function failedChecks(error: unknown): Record<string, StorageCheck> | null {
  if (!(error instanceof ApiError) || error.code !== 'STORAGE_CHECK_FAILED') return null
  const checks = (error.details as { checks?: Record<string, StorageCheck> } | undefined)?.checks
  return checks && typeof checks === 'object' ? checks : {}
}

/** 校验失败的字段（例如 ['r2', 'bucket']），用来把焦点移到出错的输入框 */
export function invalidField(error: unknown): string[] | null {
  if (!(error instanceof ApiError) || error.code !== 'VALIDATION_FAILED') return null
  const errors = (error.details as { errors?: { loc?: unknown[] }[] } | undefined)?.errors
  const loc = errors?.[0]?.loc
  return Array.isArray(loc) ? loc.filter((part): part is string => typeof part === 'string' && part !== 'body') : null
}

export const isVersionConflict = (error: unknown) => error instanceof ApiError && error.status === 409 && error.code === 'VERSION_CONFLICT'

// 这些错误的说明是工作台自己写的（不含外部内容），直接显示
const OWN = new Set(['VALIDATION_FAILED', 'STORAGE_IN_USE', 'STORAGE_CHECK_FAILED'])
export function storageError(error: unknown): string {
  if (error instanceof ApiError && OWN.has(error.code) && error.message) return error.message
  if (error instanceof ApiError && error.status === 403) return '只有 owner 能修改图片存储设置。'
  return errorMessage(error)
}

// 自检失败原因（S3 错误码或工作台的分类）的中文说明；不认识的原样显示
const CHECK_HINTS: Record<string, string> = {
  SignatureDoesNotMatch: 'Secret 填错了',
  InvalidAccessKeyId: 'Access Key ID 填错了或已被删除',
  AccessDenied: '密钥没有这个桶的读写权限',
  NoSuchBucket: '桶不存在：检查桶名（R2 还要看辖区，OSS 还要看地域）',
  AUTH_FAILED: '存储拒绝了凭证',
  connect: '连不上存储地址：检查账户 ID 或地域',
  timeout: '存储没有及时响应',
  network: '连接中断',
  probe_mismatch: '探测对象读回的内容不一致',
  local_io: '工作台服务器上的存储目录读写失败',
}
export function checkText(check: Pick<StorageCheck, 'ok' | 'reason'>): string {
  if (check.ok) return '自检通过'
  const reason = check.reason ?? ''
  const hint = CHECK_HINTS[reason]
  return reason ? `自检失败：${reason}${hint ? `（${hint}）` : ''}` : '自检失败'
}
