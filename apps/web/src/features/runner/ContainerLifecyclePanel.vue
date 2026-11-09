<script setup lang="ts">
import { computed } from 'vue'
import { Shield, ShieldCheck } from '@lucide/vue'
import { dataOf, type Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { formatTime } from '../time'
import { LIFETIME, NETWORK, RUNTIME, USER, riskyChoices, seconds, useRunnerAction, type RunnerEnv } from './api'
import { binary } from './containerMetrics'

// 详情里的“配置与回收”（T-0187）：运行配置、回收规则与下一步、保护、生命周期动作。
// 动作沿用原来的 POST /runner/environments/{id}/actions + version，服务端决定哪个状态能做什么；
// 页面只按原来的规则显示按钮。停止、归档、删除对数据的影响不同，确认框按类别写清楚。
// 动作是异步的（指令发给容器服务器执行），提示写“已发出”，结果以刷新后的容器状态为准；
// 版本冲突先刷新，结果未知（WRITE_UNKNOWN）先刷新状态，不会自动重发。
type Action = Schema['RunnerEnvAction']['action']
const props = defineProps<{ env: RunnerEnv }>()
const emit = defineEmits<{ refresh: [] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const { busy, error, notice, act, header } = useRunnerAction(scope)

const ACTION: Record<Action, string> = { start: '启动', stop: '停止', archive: '归档', delete: '删除', keep: '撤销删除', restore: '恢复', pin: '保护', unpin: '取消保护' }
const actions = computed<Action[]>(() => {
  const env = props.env, out: Action[] = []
  if (env.state === 'stopped') out.push('start')
  if (env.state === 'running' || env.state === 'creating') out.push('stop')
  if ((env.state === 'running' || env.state === 'stopped') && !env.pinned) out.push('archive')
  if (env.state === 'pending_delete') out.push('keep')
  if (env.state === 'archived' || (env.state === 'deleted' && env.lifetime === 'persistent' && env.snapshot_at)) out.push('restore')
  if (env.state !== 'deleted' && env.state !== 'pending_delete') out.push('delete')
  return out
})
const canPin = computed(() => props.env.state !== 'deleted')
const CONFIRM: Partial<Record<Action, (env: RunnerEnv) => string>> = {
  stop: env => `停止「${env.untrusted.purpose}」？容器停下，数据卷保留；正在执行的命令会中断。再用时自动启动。`,
  delete: env => env.lifetime === 'persistent'
    ? `删除常驻环境「${env.untrusted.purpose}」？会先做加密快照（保留 30 天，可以在这里恢复），然后删掉容器与数据卷。`
    : `删除环境「${env.untrusted.purpose}」？容器与数据卷都会删掉，数据不能恢复。`,
  archive: env => env.lifetime === 'persistent'
    ? `归档「${env.untrusted.purpose}」？先停止并做加密快照，然后删掉容器与数据卷；再用时自动重建并恢复快照。`
    : `归档「${env.untrusted.purpose}」？容器与数据卷会删掉（${LIFETIME[env.lifetime]}环境的数据不保留），只留配置记录，再用时按记录重建。`,
}
async function run(action: Action) {
  const env = props.env
  const ask = CONFIRM[action]
  if (ask && !window.confirm(ask(env))) return
  const ok = await act(async () => {
    dataOf(await auth.api.POST('/api/v1/runner/environments/{environment_id}/actions', { params: { header, path: { environment_id: env.id } }, body: { action, version: env.version } }))
  }, action === 'pin' || action === 'unpin' ? `已${ACTION[action]}：${env.untrusted.purpose}` : `已发出“${ACTION[action]}”：${env.untrusted.purpose}（以刷新后的容器状态为准）`)
  if (!ok) emit('refresh')
}
const conflict = computed(() => error.value && error.value.includes('刷新'))
const score = computed(() => {
  const env = props.env
  if (env.retention_infinite) return '无穷大（数据超过快照上限、重建不出来：只停止不归档）'
  return env.retention_score === null ? '—（只算运行中与已停止的）' : env.retention_score.toFixed(env.retention_score >= 100 ? 0 : 2)
})
</script>

<template>
  <div class="cs-lifecycle">
    <section class="cs-block" aria-label="运行配置">
      <h3>运行配置</h3>
      <p v-if="riskyChoices(env).length" class="form-help agent-notice" role="note">非默认选项（隔离更弱）：{{ riskyChoices(env).join('、') }}。这是智能体按任务需要选的，已通知管理员。</p>
      <dl class="cs-facts">
        <div><dt>运行时</dt><dd>{{ RUNTIME[env.runtime] }}</dd></div>
        <div><dt>类别</dt><dd>{{ LIFETIME[env.lifetime] }}环境</dd></div>
        <div><dt>用户 / 网络</dt><dd>{{ USER[env.user] }} / {{ NETWORK[env.network] }}<template v-if="env.network === 'allowlist' && env.spec.domains.length">（{{ env.spec.domains.join('、') }}）</template></dd></div>
        <div><dt>上限</dt><dd>{{ env.cpus }} 核 · {{ env.memory_mb }} MiB 内存 · 数据卷 {{ env.spec.disk_gb }} GiB（模板设置，不是硬配额）</dd></div>
        <div><dt>模板 / 镜像</dt><dd class="cs-break">{{ env.template ?? env.spec.image }}</dd></div>
        <div><dt>申请者</dt><dd>{{ env.owner_instance_id ? env.owner : '原智能体已删除（环境保留，不挂到其他智能体）' }}</dd></div>
        <div v-if="env.share !== 'none'"><dt>共享</dt><dd>同项目的智能体可以用（申请者仍是上面这个）</dd></div>
        <div v-if="env.spec.secrets.length"><dt>注入的密钥</dt><dd>{{ env.spec.secrets.join('、') }}</dd></div>
        <div><dt>创建</dt><dd>{{ formatTime(env.created_at) }}</dd></div>
      </dl>
    </section>

    <section class="cs-block" aria-label="回收规则">
      <h3>回收规则</h3>
      <dl class="cs-facts">
        <div><dt>最近使用</dt><dd>{{ formatTime(env.last_used_at) }}（共 {{ env.use_count }} 次）</dd></div>
        <div><dt>下一步</dt><dd>{{ env.next_step ?? '—' }}</dd></div>
        <div><dt>删除条件</dt><dd>{{ env.delete_when.length ? env.delete_when.join('、') : '没有（按类别的规则回收）' }}</dd></div>
        <div v-if="env.delete_after && env.state === 'pending_delete'"><dt>删除时间</dt><dd>{{ formatTime(env.delete_after) }}（之前可以撤销）</dd></div>
        <div><dt>再用时预计等待</dt><dd>{{ seconds(env.wake_seconds) }}</dd></div>
        <div><dt>保留分</dt><dd>{{ score }}</dd></div>
        <div v-if="env.snapshot_at"><dt>快照</dt><dd>{{ binary(env.snapshot_bytes) }}，{{ formatTime(env.snapshot_at) }}</dd></div>
        <div v-if="env.state_reason"><dt>原因</dt><dd class="cs-break">{{ env.state_reason }}</dd></div>
      </dl>
      <div v-if="canPin" class="cs-protect" :class="{ 'is-on': env.pinned }">
        <component :is="env.pinned ? ShieldCheck : Shield" aria-hidden="true" />
        <div>
          <strong>{{ env.pinned ? '已保护' : '保护容器' }}</strong>
          <p class="form-help">保护后不会被自动停止、归档或因资源紧张被回收，保护期间也不能归档；智能体设的删除条件到点仍会进入待删除，手动删除也仍然可以。</p>
        </div>
        <button type="button" class="secondary-button" :disabled="busy" @click="run(env.pinned ? 'unpin' : 'pin')">{{ env.pinned ? '取消保护' : '保护' }}</button>
      </div>
    </section>

    <section class="cs-block" aria-label="生命周期操作">
      <h3>操作</h3>
      <div class="form-actions cs-actions">
        <button v-for="a in actions" :key="a" type="button" class="secondary-button" :class="{ 'im-danger': a === 'delete' }" :disabled="busy" @click="run(a)">{{ ACTION[a] }}</button>
        <span v-if="!actions.length" class="form-help">这个状态没有可以做的操作。</span>
      </div>
      <p v-if="error" role="alert" class="feedback">{{ error }}<template v-if="conflict"> 已重新读取这个容器。</template></p>
      <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
    </section>
  </div>
</template>
