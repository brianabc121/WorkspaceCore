import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, createApi, dataOf, errorMessage } from '../src/services/api'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); document.cookie = 'ws_csrf=; Max-Age=0; Path=/' })
describe('契约客户端', () => {
  it('sends same-origin cookies, reads the latest CSRF cookie on each write, and scopes workspace resources', async () => {
    const requests: Request[] = []
    vi.stubGlobal('fetch', async (request: Request) => { requests.push(request); return new Response(null, { status: 204 }) })
    const api = createApi({ workspaceId: () => 'workspace-a' })
    document.cookie = 'ws_csrf=first; Path=/'
    await api.PATCH('/api/v1/me', { body: { display_name: '新名称' } })
    document.cookie = 'ws_csrf=rotated; Path=/'
    await api.DELETE('/api/v1/auth/sessions/{session_id}', { params: { path: { session_id: 'session-a' } } })
    await api.GET('/api/v1/audit-logs', { params: { header: { 'X-Workspace-Id': 'workspace-a' } } })
    expect(requests[0]!.credentials).toBe('same-origin')
    expect(new URL(requests[0]!.url).origin).toBe(window.location.origin)
    expect(requests[0]!.headers.get('X-CSRF-Token')).toBe('first')
    expect(requests[1]!.headers.get('X-CSRF-Token')).toBe('rotated')
    expect(requests[2]!.headers.get('X-CSRF-Token')).toBeNull()
    expect(requests[2]!.headers.get('X-Workspace-Id')).toBe('workspace-a')
    expect(requests[0]!.headers.get('X-Workspace-Id')).toBeNull()
  })
  it('parses unified errors and expires authenticated state on 401', async () => {
    const expired = vi.fn()
    vi.stubGlobal('fetch', async () => Response.json({ error: { code: 'UNAUTHORIZED', message: '会话过期', request_id: 'req-1', details: {} } }, { status: 401 }))
    const api = createApi({ onUnauthorized: expired })
    await expect(api.GET('/api/v1/me')).rejects.toMatchObject({ status: 401, code: 'UNAUTHORIZED', requestId: 'req-1' })
    expect(expired).toHaveBeenCalledOnce()
  })
  it('keeps invalid login credentials as a form error', async () => {
    const expired = vi.fn()
    vi.stubGlobal('fetch', async () => Response.json({ error: { code: 'INVALID_CREDENTIALS', message: '登录失败', request_id: null } }, { status: 401 }))
    await expect(createApi({ onUnauthorized: expired }).POST('/api/v1/auth/login', { body: { email: 'test@example.com', password: 'wrong' } })).rejects.toMatchObject({ code: 'INVALID_CREDENTIALS' })
    expect(expired).not.toHaveBeenCalled()
  })
  it('explains rate limits using Retry-After without retrying writes', async () => {
    const fetcher = vi.fn(async () => Response.json({ error: { code: 'RATE_LIMITED', message: '限流', request_id: 'req-2' } }, { status: 429, headers: { 'Retry-After': '45' } }))
    vi.stubGlobal('fetch', fetcher)
    const error = await createApi().POST('/api/v1/auth/logout').catch((error: unknown) => error)
    expect(errorMessage(error)).toContain('45 秒')
    expect(fetcher).toHaveBeenCalledOnce()
  })
  it('handles non-JSON failures without rendering upstream HTML', async () => {
    vi.stubGlobal('fetch', async () => new Response('<html>private proxy diagnostic</html>', { status: 502 }))
    await expect(createApi().GET('/api/v1/me')).rejects.toMatchObject({ status: 502, code: 'HTTP_ERROR' })
  })
  it('times out a write and warns that its completion is unknown', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', (request: Request) => new Promise((_, reject) => request.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))))
    const pending = createApi({ timeoutMs: 30 }).PATCH('/api/v1/me', { body: { display_name: '新名称' } }).catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(31)
    expect(errorMessage(await pending)).toContain('结果未知')
  })
  it('explains CSRF recovery and rejects empty successful JSON responses', () => {
    expect(errorMessage(new ApiError(403, 'CSRF_FAILED', 'failed'))).toContain('刷新')
    expect(() => dataOf({ data: undefined })).toThrow()
  })
})
