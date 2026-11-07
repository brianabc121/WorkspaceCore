<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { dataOf, type Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { formatTime } from '../time'
import { LIFETIME, NETWORK, RUNTIME, STATE, USER, bytes, riskyChoices, seconds, useRunnerAction, useRunnerEnvironments, type EnvFilter, type RunnerEnv } from './api'

// 环境（RUNNER_PLAN 第 11 节）：按智能体、类别、状态筛选；每个环境的用途（智能体写的，只当数据显示）、
// 运行时 / 用户 / 网络（非默认的标出来）、用量 / 上限、下一步、删除条件、唤起耗时、保留分；
// 操作：启动、停止、归档、删除、撤销删除、恢复、保护。
type Action = Schema['RunnerEnvAction']['action']
const props = defineProps<{ instances: Schema['RunnerInstanceUsage'][]; initialState?: string }>()
const emit = defineEmits<{ runs: [environmentId: string] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const filter = ref<EnvFilter>({ state: (props.initialState as RunnerEnv['state']) || undefined })
watch(() => props.initialState, (state) => { filter.value = { ...filter.value, state: (state as RunnerEnv['state']) || undefined } })
const list = useRunnerEnvironments(scope, filter)
const items = computed(() => list.data.value?.items ?? [])
const { busy, error, notice, act, header } = useRunnerAction(scope)

const ACTION: Record<Action, string> = {
  start: '启动', stop: '停止', archive: '归档', delete: '删除', keep: '撤销删除', restore: '恢复', pin: '保护', unpin: '取消保护',
}
function actions(env: RunnerEnv): Action[] {
  const out: Action[] = []
  if (env.state === 'stopped') out.push('start')
  if (env.state === 'running' || env.state === 'creating') out.push('stop')
  if ((env.state === 'running' || env.state === 'stopped') && !env.pinned) out.push('archive')
  if (env.state === 'pending_delete') out.push('keep')
  if (env.state === 'archived' || (env.state === 'deleted' && env.lifetime === 'persistent' && env.snapshot_at)) out.push('restore')
  if (env.state !== 'deleted' && env.state !== 'pending_delete') out.push('delete')
  if (env.state !== 'deleted') out.push(env.pinned ? 'unpin' : 'pin')
  return out
}
const CONFIRM: Partial<Record<Action, (env: RunnerEnv) => string>> = {
  delete: env => env.lifetime === 'persistent'
    ? `删除常驻环境「${env.untrusted.purpose}」？会先做加密快照（保留 30 天，可以在这里恢复），然后删掉容器与数据卷。`
    : `删除环境「${env.untrusted.purpose}」？容器与数据卷都会删掉，数据不能恢复。`,
  archive: env => env.lifetime === 'persistent'
    ? `归档「${env.untrusted.purpose}」？先停止并做加密快照，然后删掉容器与数据卷；再用时自动重建并恢复快照。`
    : `归档「${env.untrusted.purpose}」？容器与数据卷会删掉（会话环境的数据不保留），只留配置记录，再用时按记录重建。`,
}
async function run(env: RunnerEnv, action: Action) {
  const ask = CONFIRM[action]
  if (ask && !window.confirm(ask(env))) return
  await act(async () => {
    dataOf(await auth.api.POST('/api/v1/runner/environments/{environment_id}/actions', { params: { header, path: { environment_id: env.id } }, body: { action, version: env.version } }))
  }, `已${ACTION[action]}：${env.untrusted.purpose}`)
}
const usage = (env: RunnerEnv) => `内存 ${bytes(env.mem_bytes)} / ${env.memory_mb} MB · 磁盘 ${bytes(env.disk_bytes)} / ${env.spec.disk_gb} GB · CPU ${env.cpus} 核`
const score = (env: RunnerEnv) => env.retention_infinite ? '无穷大（数据超过快照上限、重建不出来：只停止不归档）'
  : env.retention_score === null ? '—（只算运行中与已停止的）' : env.retention_score.toFixed(env.retention_score >= 100 ? 0 : 2)
</script>

<template>
  <section class="panel runner-card" aria-label="筛选环境">
    <form class="runner-filters" @submit.prevent>
      <label class="form-field"><span>智能体</span><select v-model="filter.instance_id"><option :value="undefined">全部</option><option v-for="i in instances" :key="i.instance_id" :value="i.instance_id">{{ i.name }}</option></select></label>
      <label class="form-field"><span>类别</span><select v-model="filter.lifetime"><option :value="undefined">全部</option><option v-for="(name, key) in LIFETIME" :key="key" :value="key">{{ name }}</option></select></label>
      <label class="form-field"><span>状态</span><select v-model="filter.state"><option :value="undefined">全部（不含已删除）</option><option v-for="(name, key) in STATE" :key="key" :value="key">{{ name }}</option></select></label>
      <label class="check-field"><input v-model="filter.include_deleted" type="checkbox" /> 包含已删除</label>
    </form>
  </section>
  <p v-if="error" role="alert" class="feedback">{{ error }}</p>
  <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
  <p v-if="list.isPending.value" role="status">正在加载环境…</p>
  <p v-else-if="list.error.value" role="alert">加载失败，请稍后重试。</p>
  <p v-else-if="!items.length" class="panel empty-state">没有符合条件的环境。</p>
  <ul v-else class="runner-list" aria-label="环境列表">
    <li v-for="env in items" :key="env.id">
      <article class="panel runner-card" :aria-label="`环境：${env.untrusted.purpose}`">
        <div class="section-heading">
          <h3><span class="preserve-text runner-title">{{ env.untrusted.purpose }}</span> <span class="badge">{{ STATE[env.state] }}</span> <span class="badge">{{ LIFETIME[env.lifetime] }}</span><span v-if="env.pinned" class="badge">已保护</span></h3>
          <div class="form-actions runner-actions">
            <button v-for="a in actions(env)" :key="a" type="button" class="secondary-button" :class="{ 'im-danger': a === 'delete' }" :disabled="busy" @click="run(env, a)">{{ ACTION[a] }}</button>
            <button type="button" class="secondary-button" @click="emit('runs', env.id)">运行记录</button>
          </div>
        </div>
        <p v-if="riskyChoices(env).length" class="form-help agent-notice" role="note">非默认选项（隔离更弱）：{{ riskyChoices(env).join('、') }}。这是智能体按任务需要选的，已通知管理员。</p>
        <dl class="agent-facts">
          <div><dt>智能体</dt><dd>{{ env.owner }}</dd></div>
          <div><dt>模板</dt><dd>{{ env.template ?? env.spec.image }}</dd></div>
          <div><dt>运行时 / 用户 / 网络</dt><dd>{{ RUNTIME[env.runtime] }} / {{ USER[env.user] }} / {{ NETWORK[env.network] }}<template v-if="env.network === 'allowlist' && env.spec.domains.length">（{{ env.spec.domains.join('、') }}）</template></dd></div>
          <div><dt>用量 / 上限</dt><dd>{{ usage(env) }}</dd></div>
          <div><dt>上次使用</dt><dd>{{ formatTime(env.last_used_at) }}（共 {{ env.use_count }} 次）</dd></div>
          <div><dt>下一步</dt><dd>{{ env.next_step ?? '—' }}</dd></div>
          <div><dt>删除条件</dt><dd>{{ env.delete_when.length ? env.delete_when.join('、') : '没有（按类别的规则回收）' }}</dd></div>
          <div><dt>预计唤起耗时</dt><dd>{{ seconds(env.wake_seconds) }}</dd></div>
          <div><dt>保留分</dt><dd>{{ score(env) }}</dd></div>
          <div v-if="env.snapshot_at"><dt>快照</dt><dd>{{ bytes(env.snapshot_bytes) }}，{{ formatTime(env.snapshot_at) }}</dd></div>
          <div v-if="env.delete_after && env.state === 'pending_delete'"><dt>删除时间</dt><dd>{{ formatTime(env.delete_after) }}</dd></div>
          <div v-if="env.spec.secrets.length"><dt>注入的密钥</dt><dd>{{ env.spec.secrets.join('、') }}</dd></div>
        </dl>
        <p v-if="env.state_reason" class="form-help">原因：{{ env.state_reason }}</p>
      </article>
    </li>
  </ul>
</template>
