<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { useAgentAction } from './action'
import { OPS_ACTION, OPS_HELP, OPS_SERVICE, OPS_STATUS, OPS_STEP_UP, opsError, opsKey, opsUnavailable, useAgentOps, useOpsAction, type OpsAction, type OpsService } from './ops'

// 实例卡片里的“运维”（T-0114）：固定的运维动作，经工作台的 SSH 通道执行；输出脱敏后保存 7 天。
// 重启、重新拉起、清理镜像要再输一次自己的登录密码；同一实例同时只跑一个动作。
const props = defineProps<{ instance: Schema['AgentInstanceOut'] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, cache = useQueryClient()
const ops = useAgentOps(scope, () => props.instance.id)
const selected = ref<string | null>(null)
const detail = useOpsAction(scope, () => props.instance.id, () => selected.value)
const { busy, error, notice, failure, act, refresh, header } = useAgentAction(scope, opsError)
const service = ref<OpsService>('openclaw'), confirming = ref<OpsAction | null>(null), password = ref('')
const state = computed(() => ops.data.value)
const running = computed(() => state.value?.running ?? null)
const shown = computed(() => detail.data.value?.id === selected.value ? detail.data.value : null)
const path = computed(() => ({ instance_id: props.instance.id }))
const direct: OpsAction[] = ['status', 'logs', 'report']

// 打开面板时有动作在跑：直接看它的进度
watch(running, now => { if (now && !selected.value) selected.value = now.id }, { immediate: true })
// 看着的动作结束了：刷新列表（状态、退出码）
watch(() => shown.value?.status, (now, before) => {
  if (before === 'running' && now && now !== 'running') void cache.invalidateQueries({ queryKey: opsKey(user, scope, props.instance.id) })
})

async function start(action: OpsAction, secret?: string) {
  let started: Schema['AgentOpsActionOut'] | undefined
  const body: Schema['AgentOpsRun'] = { action, ...(action === 'logs' ? { service: service.value } : {}), ...(secret ? { password: secret } : {}) }
  const ok = await act(async () => {
    started = dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/ops', { params: { header, path: path.value }, body }))
  }, `已开始：${OPS_ACTION[action]}`)
  if (ok && started) selected.value = started.id
  else if (failure.value instanceof ApiError && failure.value.code === 'WRITE_UNKNOWN') void refresh()
  return ok
}
function ask(action: OpsAction) { confirming.value = action; password.value = ''; error.value = '' }
async function confirm() {
  const action = confirming.value
  // 密码只随这一次请求发出，发出后立即从页面上清掉
  const secret = password.value
  password.value = ''
  if (!action || !secret) return
  if (await start(action, secret)) confirming.value = null
}
</script>

<template>
  <section class="agent-section ops-panel" aria-label="运维">
    <h3>运维</h3>
    <p v-if="ops.isPending.value" role="status">正在加载…</p>
    <p v-else-if="ops.error.value" role="alert">{{ opsError(ops.error.value) }}</p>
    <template v-else-if="state">
      <p v-if="!state.available" class="form-help">{{ opsUnavailable(state.reason) }}</p>
      <template v-else>
        <p class="form-help">命令写死在工作台里，只作用于这个实例的接入包目录；输出脱敏后保存 7 天，每次都写审计。</p>
        <div class="ops-actions">
          <label class="form-field"><span>日志来源</span><select v-model="service"><option v-for="(label, key) in OPS_SERVICE" :key="key" :value="key">{{ label }}</option></select></label>
          <button v-for="action in direct" :key="action" type="button" class="secondary-button" :title="OPS_HELP[action]" :disabled="busy || !!running" @click="start(action)">{{ OPS_ACTION[action] }}</button>
          <button v-for="action in OPS_STEP_UP" :key="action" type="button" class="secondary-button im-danger" :title="OPS_HELP[action]" :disabled="busy || !!running" @click="ask(action)">{{ OPS_ACTION[action] }}</button>
        </div>
        <form v-if="confirming" class="im-group-form ops-confirm" :aria-label="`确认：${OPS_ACTION[confirming]}`" @submit.prevent="confirm">
          <p><strong>{{ OPS_ACTION[confirming] }}</strong>（{{ OPS_HELP[confirming] }}）：输入你的登录密码确认。</p>
          <label class="form-field"><span>你的登录密码</span><input v-model="password" type="password" autocomplete="current-password" maxlength="128" /></label>
          <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy || !password">确认执行</button><button type="button" class="secondary-button" @click="confirming = null; password = ''">取消</button></div>
        </form>
      </template>
      <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
      <article v-if="shown" class="ops-result" :aria-label="`运维结果：${OPS_ACTION[shown.action]}`">
        <p>
          <strong>{{ OPS_ACTION[shown.action] }}<template v-if="shown.service">（{{ OPS_SERVICE[shown.service] }}）</template></strong>
          <span class="badge">{{ OPS_STATUS[shown.status] }}</span>
          <span class="form-help">开始于 {{ formatTime(shown.started_at) }}<template v-if="shown.exit_code !== null"> · 退出码 {{ shown.exit_code }}</template></span>
        </p>
        <p v-if="shown.status === 'running'" role="status">正在执行…</p>
        <p v-if="shown.error_message" class="feedback">{{ shown.error_message }}</p>
        <p v-if="shown.truncated" class="form-help">输出超过 64 KB，只保留了末尾。</p>
        <pre v-if="shown.output" class="ai-arguments ops-output" tabindex="0" aria-label="输出（令牌、密钥、私钥已替换成 ***）">{{ shown.output }}</pre>
      </article>
      <details v-if="state.items.length" class="ops-history">
        <summary>最近的运维记录（{{ state.items.length }}，保留 7 天）</summary>
        <ul class="im-contact-list">
          <li v-for="item in state.items" :key="item.id">
            <span>{{ formatTime(item.started_at) }} · {{ OPS_ACTION[item.action] }}<template v-if="item.service">（{{ OPS_SERVICE[item.service] }}）</template> · {{ OPS_STATUS[item.status] }}<template v-if="item.exit_code !== null"> · 退出码 {{ item.exit_code }}</template></span>
            <button type="button" class="secondary-button" :aria-pressed="selected === item.id" @click="selected = item.id">查看输出</button>
          </li>
        </ul>
      </details>
    </template>
  </section>
</template>
