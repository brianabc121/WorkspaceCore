<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { LIMIT_FIELDS, runnerError, useRunnerAction } from '../runner/api'

// 实例卡片里的“运行环境权限”（T-0140，RUNNER_PLAN 第 8.4、11 节，例外八）：开关（默认关闭）、限额、可以注入的密钥。
// 打开后它可以在容器服务器上建、用、停、删自己的容器环境并在里面执行命令，直接执行、不审批；
// 关掉时它所有运行中的环境都会停止。可以注入的密钥就是密钥库已授权给这个实例的那些。
const props = defineProps<{ instance: Schema['AgentInstanceOut'] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const { busy, error, notice, act, header } = useRunnerAction(scope)
const runner = useQuery({
  queryKey: computed(() => ['agents', user, scope, 'runner', props.instance.id] as const),
  enabled: computed(() => user === auth.user?.id && scope === auth.workspaceId && auth.canManage),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/agents/instances/{instance_id}/runner', { params: { header, path: { instance_id: props.instance.id } }, signal })),
})
const state = computed(() => runner.data.value)
const limits = ref<Record<string, string>>({}), editing = ref(false)
watch(state, (value) => { if (value) limits.value = Object.fromEntries(LIMIT_FIELDS.map(f => [f.key, String(value.limits[f.key] ?? '')])) }, { immediate: true })
const readOnly = computed(() => props.instance.scope !== 'read_write')
const summary = computed(() => {
  const v = state.value
  if (!v) return ''
  if (!v.enabled) return '关闭'
  return `已打开：最多同时 ${v.limits.envs ?? '—'} 个环境（常驻 ${v.limits.persistent ?? '—'} 个）、内存 ${v.limits.memory_mb ?? '—'} MB、每小时 ${v.limits.execs_per_hour ?? '—'} 次命令`
})

function parsed(): Record<string, number> | string {
  const out: Record<string, number> = {}
  for (const field of LIMIT_FIELDS) {
    const raw = String(limits.value[field.key] ?? '').trim()
    if (!raw) continue
    const value = Number(raw)
    if (!Number.isFinite(value) || value < 0) return `${field.label} 要填不小于 0 的数字`
    out[field.key] = value
  }
  return out
}
async function save(enabled: boolean) {
  const v = state.value
  if (!v) return
  const body = parsed()
  if (typeof body === 'string') { error.value = body; return }
  if (enabled && !v.enabled && !window.confirm(`给「${props.instance.name}」打开运行环境权限？它可以在容器服务器的容器里执行任意命令、读到注入给它的密钥，直接执行、不审批；每次都有执行记录，非默认选项会通知管理员。`)) return
  if (!enabled && v.enabled && !window.confirm(`关闭「${props.instance.name}」的运行环境权限？它所有运行中的环境都会停止（数据卷保留）。`)) return
  const ok = await act(async () => {
    dataOf(await auth.api.PUT('/api/v1/agents/instances/{instance_id}/runner', { params: { header, path: { instance_id: props.instance.id } }, body: { enabled, limits: body, version: v.version } }))
  }, enabled ? (v.enabled ? '已保存限额' : '已打开运行环境权限') : '已关闭运行环境权限，它的环境正在停止')
  if (ok) editing.value = false
}
</script>

<template>
  <section class="agent-section" aria-label="运行环境权限">
    <div class="section-heading">
      <h3>运行环境权限：{{ runner.isPending.value ? '…' : summary }}</h3>
      <div v-if="state" class="form-actions">
        <button v-if="state.enabled" type="button" class="secondary-button" :disabled="busy" @click="editing = !editing">{{ editing ? '收起限额' : '改限额' }}</button>
        <button type="button" class="secondary-button" :class="{ 'im-danger': state.enabled }" :disabled="busy || (!state.enabled && (readOnly || !!instance.disabled_at))" @click="save(!state.enabled)">{{ state.enabled ? '关闭运行环境权限' : '允许它在容器服务器上建环境、执行命令' }}</button>
      </div>
    </div>
    <p v-if="runner.error.value" role="alert">{{ runnerError(runner.error.value) }}</p>
    <p v-if="error" role="alert" class="feedback">{{ error }}</p>
    <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
    <form v-if="state && (editing || !state.enabled)" class="runner-filters" aria-label="运行环境的限额" @submit.prevent="save(state.enabled)">
      <label v-for="f in LIMIT_FIELDS" :key="f.key" class="form-field"><span>{{ f.label }}</span><input v-model="limits[f.key]" type="number" min="0" :step="f.step ?? 1" /></label>
      <div v-if="state.enabled" class="form-actions"><button type="submit" class="primary-button" :disabled="busy">保存限额</button></div>
    </form>
    <p v-if="state" class="form-help">
      打开后它用 runner_* / env_* 工具自己决定镜像、安装步骤、运行时、用户、网络（比如要跑安卓模拟器就选 kvm），直接执行、不审批；不能用特权模式、挂 Docker socket 或宿主目录、用宿主网络，也连不到私有网段、工作台与 OpenClaw 服务器。
      可以注入的密钥：{{ state.secrets.length ? state.secrets.join('、') : '没有（在下面的“高级凭据授权”里授权给它的密钥才能注入）' }}。
      <template v-if="readOnly && !state.enabled"> 这个实例的工具权限是只读，打不开。</template>
      <template v-if="state.enabled && state.enabled_at"> 打开于 {{ formatTime(state.enabled_at) }}。</template>
      它申请的容器、负载、执行记录与回收在 <RouterLink to="/runner">容器服务器</RouterLink> 页面。
    </p>
  </section>
</template>
