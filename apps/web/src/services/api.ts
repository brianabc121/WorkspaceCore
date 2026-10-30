import createClient from 'openapi-fetch'
import type { components, paths } from './schema'

export type Schema = components['schemas']

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId: string | null = null,
    public retryAfter: number | null = null,
    public details: Schema['ErrorBody']['details'] = {},
  ) { super(message) }
}

export function isAccessError(error: unknown) {
  return error instanceof ApiError && [401, 403, 404].includes(error.status)
}

export function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return '请求失败，请稍后重试。'
  const messages: Record<string, string> = {
    INVALID_CREDENTIALS: '邮箱或密码错误，请重试。',
    UNAUTHORIZED: '登录已过期，请重新登录。',
    FORBIDDEN: '你没有执行此操作的权限。',
    CSRF_FAILED: '安全校验失败，请刷新页面后重试；仍然失败时请重新登录。',
    SETUP_TOKEN_INVALID: '安装凭据无效，请检查部署时生成的凭据。',
    SETUP_COMPLETED: '安装已完成，请前往登录。',
    INVALID_CURRENT_PASSWORD: '当前密码错误。',
    VALIDATION_FAILED: '输入内容不符合要求，请检查后重试。',
    NOT_FOUND: '资源不存在，或你已无权访问。',
    NOT_IMPLEMENTED: '此功能暂不可用。',
    NETWORK_ERROR: '网络连接失败，请检查连接后重试。',
    WRITE_UNKNOWN: '连接中断或超时，操作结果未知。请刷新确认结果，再决定是否重试。',
    TIMEOUT: '请求超时，请稍后重试。',
    HTTP_ERROR: '服务暂时不可用，请稍后重试。',
    INVALID_RESPONSE: '服务响应异常，请刷新重试。',
    STALE_SESSION: '登录状态已变化，请刷新页面后重试。',
  }
  if (error.status === 429) return error.retryAfter === null ? '请求过于频繁，请稍后重试。' : `请求过于频繁，请在 ${error.retryAfter} 秒后重试。`
  return messages[error.code] ?? '请求失败，请稍后重试。'
}

function csrfCookie() {
  const value = document.cookie.split('; ').find((item) => item.startsWith('ws_csrf='))?.slice(8)
  try { return value ? decodeURIComponent(value) : null } catch { return null }
}

export function createApi(options: { workspaceId?: () => string | null; sessionVersion?: () => number; onUnauthorized?: () => void; timeoutMs?: number } = {}) {
  const client = createClient<paths>({
    // The origin is never configurable; all operation paths come from our OpenAPI.
    baseUrl: window.location.origin,
    credentials: 'same-origin',
    fetch: async (request: Request) => {
      const version = options.sessionVersion?.()
      const write = !['GET', 'HEAD', 'OPTIONS'].includes(request.method)
      const controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? 15000)
      const abort = () => controller.abort()
      request.signal.addEventListener('abort', abort, { once: true })
      if (request.signal.aborted) controller.abort()
      try {
        // Read the entire body inside the timeout, including slow response streams.
        const response = await fetch(new Request(request, { signal: controller.signal }))
        const body = response.status === 204 ? null : await response.text()
        if (version !== options.sessionVersion?.()) throw new ApiError(0, 'STALE_SESSION', '登录状态已变化')
        if (!response.ok) {
          let detail: Partial<Schema['ErrorBody']> = {}
          try { detail = (JSON.parse(body ?? '') as Schema['ErrorResponse']).error ?? {} } catch { /* A gateway may return HTML. */ }
          const retry = response.headers.get('Retry-After')
          const seconds = retry !== null && /^\d+$/.test(retry) ? Number(retry) : null
          const error = new ApiError(response.status, detail.code ?? 'HTTP_ERROR', detail.message ?? '请求失败', detail.request_id ?? response.headers.get('X-Request-ID'), seconds, detail.details)
          if (response.status === 401 && new URL(request.url).pathname !== '/api/v1/auth/login') options.onUnauthorized?.()
          throw error
        }
        return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers })
      } catch (error) {
        if (error instanceof ApiError) throw error
        throw new ApiError(0, write ? 'WRITE_UNKNOWN' : controller.signal.aborted ? 'TIMEOUT' : 'NETWORK_ERROR', '请求未完成')
      } finally {
        clearTimeout(timeout)
        request.signal.removeEventListener('abort', abort)
      }
    },
  })
  client.use({
    onRequest({ request }) {
      if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
        const token = csrfCookie()
        if (token) request.headers.set('X-CSRF-Token', token)
      }
      const path = new URL(request.url).pathname
      if (path === '/api/v1/audit-logs' || path.startsWith('/api/v1/workspaces/')) {
        const workspaceId = options.workspaceId?.()
        if (workspaceId && !request.headers.has('X-Workspace-Id')) request.headers.set('X-Workspace-Id', workspaceId)
      }
      return request
    },
  })
  return client
}

export function dataOf<T>(result: { data?: T }): T {
  if (result.data === undefined) throw new ApiError(502, 'INVALID_RESPONSE', '缺少响应数据')
  return result.data
}
