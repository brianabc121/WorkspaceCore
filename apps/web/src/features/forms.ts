import { ref } from 'vue'
import { z } from 'zod'
import { errorMessage, type Schema } from '../services/api'

const name = z.string().trim().min(1, '请输入名称').max(64, '名称最多 64 个字符')
const password = z.string().min(12, '密码至少 12 个字符').max(128, '密码最多 128 个字符')
export const loginSchema = z.object({ email: z.string().email('请输入有效邮箱'), password: z.string().min(1, '请输入密码').max(128) }) satisfies z.ZodType<Schema['LoginRequest']>
export const setupSchema = loginSchema.extend({ password, setup_token: z.string().min(1, '请输入安装凭据').max(256), display_name: name, workspace_name: name }) satisfies z.ZodType<Schema['SetupRequest']>
export const profileSchema = z.object({ display_name: name }) satisfies z.ZodType<Schema['MeUpdate']>
export const workspaceSchema = z.object({ name }) satisfies z.ZodType<Schema['WorkspaceUpdate']>
export const passwordSchema = z.object({ current_password: z.string().min(1, '请输入当前密码').max(128), new_password: password }) satisfies z.ZodType<Schema['PasswordChangeRequest']>

export function useAction() {
  const pending = ref(false)
  const error = ref('')
  const notice = ref('')
  function clear() { error.value = ''; notice.value = '' }
  async function run(action: () => Promise<void>, success = '') {
    if (pending.value) return false
    pending.value = true
    clear()
    try { await action(); notice.value = success; return true }
    catch (cause) { error.value = errorMessage(cause); return false }
    finally { pending.value = false }
  }
  return { pending, error, notice, clear, run }
}
