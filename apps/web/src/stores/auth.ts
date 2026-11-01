import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ApiError, createApi, dataOf, type Schema } from '../services/api'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<Schema['UserOut'] | null>(null)
  const workspaces = ref<Schema['WorkspaceOut'][]>([])
  const workspaceId = ref<string | null>(null)
  const setupRequired = ref(false)
  const initialized = ref(false)
  const loading = ref(false)
  const initializationError = ref<unknown>(null)
  const currentWorkspace = computed(() => workspaces.value.find((item) => item.id === workspaceId.value) ?? null)
  const canManage = computed(() => ['owner', 'admin'].includes(currentWorkspace.value?.role ?? ''))
  const isOwner = computed(() => currentWorkspace.value?.role === 'owner')
  let pending: Promise<void> | null = null
  let sessionVersion = 0
  function clearSession() { sessionVersion++; user.value = null; workspaces.value = []; workspaceId.value = null }
  const api = createApi({ workspaceId: () => workspaceId.value, sessionVersion: () => sessionVersion, onUnauthorized: clearSession })
  function acceptSession(me: Schema['MeResponse']) {
    sessionVersion++
    user.value = me.user
    workspaces.value = me.workspaces
    workspaceId.value = me.workspaces.some((item) => item.id === workspaceId.value) ? workspaceId.value : me.workspaces[0]?.id ?? null
    setupRequired.value = false
    initialized.value = true
  }
  async function initialize() {
    if (initialized.value) return
    if (pending) return pending
    loading.value = true
    initializationError.value = null
    pending = (async () => {
      try {
        setupRequired.value = dataOf(await api.GET('/api/v1/setup/status')).setup_required
        if (!setupRequired.value) {
          try { acceptSession(dataOf(await api.GET('/api/v1/me'))) }
          catch (error) { if (!(error instanceof ApiError && error.status === 401)) throw error }
        }
        initialized.value = true
      } catch (error) { initializationError.value = error; throw error }
      finally { loading.value = false; pending = null }
    })()
    return pending
  }
  function selectWorkspace(id: string) {
    if (workspaces.value.some((item) => item.id === id)) workspaceId.value = id
  }
  async function login(body: Schema['LoginRequest']) { acceptSession(dataOf(await api.POST('/api/v1/auth/login', { body }))) }
  async function setup(body: Schema['SetupRequest']) { acceptSession(dataOf(await api.POST('/api/v1/setup', { body }))) }
  async function logout() { await api.POST('/api/v1/auth/logout'); clearSession() }
  async function changePassword(body: Schema['PasswordChangeRequest']) {
    await api.POST('/api/v1/auth/password', { body })
    sessionVersion++
  }
  return { user, workspaces, workspaceId, currentWorkspace, canManage, isOwner, setupRequired, initialized, loading, initializationError, api, initialize, acceptSession, clearSession, selectWorkspace, login, setup, logout, changePassword }
})
