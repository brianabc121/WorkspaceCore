import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '../src/stores/auth'
import { useTaskOperations } from '../src/stores/taskOperations'
import { fail, me, mockApi } from './helpers'
import { project, task } from './sprint3-fixtures'

beforeEach(() => { setActivePinia(createPinia()); useAuthStore().acceptSession(me) })
describe('任务幂等与结果未知', () => {
  it('does not replay an unknown write after the server idempotency retention window', async () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000)
    const requests = mockApi(() => fail(504, 'RESULT_UNKNOWN'))
    const store = useTaskOperations()
    await store.submit({ method: 'POST', body: { project_id: project.id, title: '任务', description: '', priority: 0 } })
    now.mockReturnValue(1000 + 24 * 60 * 60 * 1000)
    await store.retry()
    expect(requests).toHaveLength(1)
    expect(store.current?.status).toBe('conflict')
    expect(store.current?.error).toMatchObject({ code: 'IDEMPOTENCY_EXPIRED' })
  })
  it.each(['gateway', 'empty-success', 'malformed-success'])('retains the original key after an uncertain %s response', async (kind) => {
    let count = 0
    const requests = mockApi(() => ++count > 1 ? Response.json(task, { status: 201 }) : kind === 'gateway' ? new Response('<html>gateway failed</html>', { status: 502 }) : kind === 'empty-success' ? new Response(null, { status: 204 }) : new Response('{invalid', { status: 201, headers: { 'Content-Type': 'application/json' } }))
    const store = useTaskOperations()
    await store.submit({ method: 'POST', body: { project_id: project.id, title: '任务', description: '', priority: 0 } })
    expect(store.current?.status).toBe('unknown')
    await store.retry()
    expect(requests[1]!.headers.get('Idempotency-Key')).toBe(requests[0]!.headers.get('Idempotency-Key'))
    expect(store.current?.status).toBe('succeeded')
  })
  it('queries an unknown creation with the same key and frozen payload, without duplicate clicks', async () => {
    let finish: ((response: Response) => void) | undefined
    let count = 0
    const requests = mockApi(() => ++count === 1 ? new Promise<Response>((resolve) => { finish = resolve }) : Response.json(task, { status: 201 }))
    const store = useTaskOperations()
    const command = { method: 'POST' as const, body: { project_id: project.id, title: '原始标题', description: '', priority: 0 } }
    const pending = store.submit(command)
    await vi.waitFor(() => expect(finish).toBeDefined())
    await store.submit(command)
    expect(requests).toHaveLength(1)
    finish!(fail(504, 'RESULT_UNKNOWN')); await pending
    expect(store.current?.status).toBe('unknown')
    command.body.title = '不应重放的新内容'
    await store.retry()
    expect(store.current?.status).toBe('succeeded')
    expect(requests[1]!.headers.get('Idempotency-Key')).toBe(requests[0]!.headers.get('Idempotency-Key'))
    expect(await requests[1]!.clone().json()).toEqual({ project_id: project.id, title: '原始标题', description: '', priority: 0 })
    expect(requests[1]!.headers.get('X-Workspace-Id')).toBe('workspace-a')
  })
  it('requires explicit confirmation to use a new key after OPERATION_NOT_APPLIED', async () => {
    let count = 0
    const requests = mockApi(() => ++count === 1 ? fail(504, 'RESULT_UNKNOWN') : count === 2 ? fail(409, 'OPERATION_NOT_APPLIED') : Response.json(task, { status: 201 }))
    const store = useTaskOperations()
    await store.submit({ method: 'POST', body: { project_id: project.id, title: '任务', description: '', priority: 0 } })
    await store.retry()
    expect(store.current?.status).toBe('not_applied')
    await store.retry()
    expect(requests).toHaveLength(2)
    await store.confirmResubmit()
    expect(requests[2]!.headers.get('Idempotency-Key')).not.toBe(requests[0]!.headers.get('Idempotency-Key'))
  })
  it('retains unknown operations across workspace switches and clears them on logout', async () => {
    mockApi(() => fail(503, 'EXTERNAL_UNAVAILABLE'))
    const auth = useAuthStore()
    auth.acceptSession({ ...me, workspaces: [...me.workspaces, { ...me.workspaces[0]!, id: 'workspace-b' }] })
    const store = useTaskOperations()
    await store.submit({ method: 'PATCH', id: task.id, body: { version: task.version, done: true } })
    auth.selectWorkspace('workspace-b'); expect(store.current).toBeNull()
    auth.selectWorkspace('workspace-a'); expect(store.current?.status).toBe('unknown')
    auth.clearSession(); expect(store.current).toBeNull()
  })
  it('keeps conflicts blocked until reload and does not treat external auth failure as logout', async () => {
    mockApi(() => fail(409, 'VERSION_CONFLICT'))
    const store = useTaskOperations()
    await store.submit({ method: 'PATCH', id: task.id, body: { version: task.version, title: '改名' } })
    expect(store.current?.status).toBe('conflict')
    const requests = mockApi(() => fail(502, 'INTEGRATION_UNAUTHORIZED'))
    await store.retry(); expect(requests).toHaveLength(0)
    store.clearResolved()
    await store.submit({ method: 'DELETE', id: task.id, version: task.version })
    expect(store.current?.status).toBe('unknown')
    expect(useAuthStore().user).not.toBeNull()
  })
})
