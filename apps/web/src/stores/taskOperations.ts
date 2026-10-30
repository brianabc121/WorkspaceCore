import { computed, reactive, watch } from 'vue'
import { defineStore } from 'pinia'
import { useAuthStore } from './auth'
import { ApiError, dataOf, type Schema } from '../services/api'

export type TaskCommand = { method: 'POST'; body: Schema['TaskCreate'] } | { method: 'PATCH'; id: string; body: Schema['TaskUpdate'] } | { method: 'DELETE'; id: string; version: string }
type Status = 'pending' | 'unknown' | 'conflict' | 'not_applied' | 'failed' | 'succeeded'
type Operation = { key: string; scope: string; createdAt: number; command: TaskCommand; status: Status; error: unknown; result: Schema['TaskOut'] | null }
export const useTaskOperations = defineStore('task-operations', () => {
  const auth = useAuthStore()
  const operations = reactive<Record<string, Operation>>({})
  const scopeKey = computed(() => `${auth.user?.id}:${auth.workspaceId}`)
  const current = computed(() => auth.user && auth.workspaceId ? operations[scopeKey.value] ?? null : null)
  const locked = computed(() => !!current.value && ['pending', 'unknown', 'conflict', 'not_applied'].includes(current.value.status))
  watch(() => auth.user?.id, () => { for (const key in operations) delete operations[key] }, { flush: 'sync' })
  function allowed() { return !!auth.user && !!auth.currentWorkspace && auth.currentWorkspace.role !== 'viewer' }
  async function execute(op: Operation) {
    op.status = 'pending'; op.error = null
    const header = { 'X-Workspace-Id': op.scope, 'Idempotency-Key': op.key }
    try {
      const command = op.command
      if (command.method === 'POST') op.result = dataOf(await auth.api.POST('/api/v1/tasks', { params: { header }, body: command.body }))
      else if (command.method === 'PATCH') op.result = dataOf(await auth.api.PATCH('/api/v1/tasks/{task_id}', { params: { header, path: { task_id: command.id } }, body: command.body }))
      else { await auth.api.DELETE('/api/v1/tasks/{task_id}', { params: { header, path: { task_id: command.id }, query: { version: command.version } } }); op.result = null }
      op.status = 'succeeded'
      return true
    } catch (error) {
      op.error = error
      const code = error instanceof ApiError ? error.code : 'WRITE_UNKNOWN'
      const uncertain = !(error instanceof ApiError) || error.status === 0 || error.status >= 500 || ['RESULT_UNKNOWN', 'IDEMPOTENCY_IN_PROGRESS'].includes(code)
      op.status = code === 'OPERATION_NOT_APPLIED' ? 'not_applied' : ['VERSION_CONFLICT', 'IDEMPOTENCY_KEY_REUSED'].includes(code) ? 'conflict' : uncertain ? 'unknown' : 'failed'
      return false
    }
  }
  async function submit(command: TaskCommand) {
    if (!allowed() || locked.value) return false
    operations[scopeKey.value] = { key: crypto.randomUUID(), scope: auth.workspaceId!, createdAt: Date.now(), command: JSON.parse(JSON.stringify(command)) as TaskCommand, status: 'pending', error: null, result: null }
    return execute(operations[scopeKey.value]!)
  }
  async function retry() {
    const op = current.value
    if (!allowed() || !op || op.status !== 'unknown') return false
    if (Date.now() - op.createdAt >= 24 * 60 * 60 * 1000) { op.status = 'conflict'; op.error = new ApiError(409, 'IDEMPOTENCY_EXPIRED', ''); return false }
    return execute(op)
  }
  async function confirmResubmit() {
    const op = current.value
    if (!allowed() || !op || op.status !== 'not_applied') return false
    op.key = crypto.randomUUID(); op.createdAt = Date.now()
    return execute(op)
  }
  function clearResolved() {
    if (current.value && !['pending', 'unknown', 'not_applied'].includes(current.value.status)) delete operations[scopeKey.value]
  }
  return { current, locked, submit, retry, confirmResubmit, clearResolved }
})
