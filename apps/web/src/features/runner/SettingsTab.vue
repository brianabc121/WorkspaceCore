<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { dataOf, type Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { formatTime } from '../time'
import { HOST_STATUS, POLICY_FIELDS, downloadRunnerBundle, useRunnerAction } from './api'
import RunnerSshDeploy from './RunnerSshDeploy.vue'
import ModuleServerPanels from '../../modules/ModuleServerPanels.vue'

// 设置（RUNNER_PLAN 第 9、11 节）：添加、重新部署（重新下载接入包，旧凭证立即失效）、移除容器服务器；
// 回收策略（各类别时长、水位、快照上限），保存带版本号，防止两个人同时改。
// “通过 SSH 部署”后面是插拔式模块提供的区块（例如大模型客户端的“MCP 管理器”，T-0233）：经 ModuleServerPanels 渲染，这里不导入模块的代码。
const props = defineProps<{ host: Schema['RunnerHostOut'] | null }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const { busy, error, notice, act, header } = useRunnerAction(scope)
const name = ref('容器服务器'), ingress = ref<Schema['RunnerHostCreate']['ingress']>('intranet')

async function add() {
  await act(async () => {
    dataOf(await auth.api.POST('/api/v1/runner/hosts', { params: { header }, body: { name: name.value.trim(), ingress: ingress.value, backend: 'docker' } }))
  }, '已添加：下载接入包，放到容器服务器上运行 install.sh')
}
async function bundle() {
  if (props.host?.last_seen_at && !window.confirm('重新下载接入包会签发新凭证，旧凭证立即失效：容器服务器要用新的接入包重新部署才能连上。继续？')) return
  let file = ''
  if (await act(async () => { file = await downloadRunnerBundle(scope, props.host!.id) })) notice.value = `已下载 ${file}：凭证只在这个 zip 里，传到容器服务器后删掉本地的副本`
}
async function remove() {
  const host = props.host
  if (!host || !window.confirm(`移除容器服务器「${host.name}」？凭证立即失效。服务器上的 runner-agent 与 Docker 不会被卸载。`)) return
  await act(async () => {
    await auth.api.DELETE('/api/v1/runner/hosts/{host_id}', { params: { header, path: { host_id: host.id }, query: { version: host.version } } })
  }, '已移除容器服务器')
}

// 回收策略：显示生效值，只提交改过的（没改的跟着默认值走）
const values = ref<Record<string, string>>({})
const reset = () => { values.value = Object.fromEntries(POLICY_FIELDS.map(f => [f.key, String(props.host?.policy[f.key] ?? '')])) }
watch(() => props.host?.version, reset, { immediate: true })
const groups = computed(() => [...new Set(POLICY_FIELDS.map(f => f.group))].map(group => ({ group, fields: POLICY_FIELDS.filter(f => f.group === group) })))
async function savePolicy() {
  const policy: Record<string, number> = {}
  for (const field of POLICY_FIELDS) {
    const raw = String(values.value[field.key] ?? '').trim()
    if (!raw) continue
    const value = Number(raw)
    if (!Number.isFinite(value) || value < 0) { error.value = `${field.label} 要填不小于 0 的数字`; return }
    if (field.step === 0.01 && value > 1) { error.value = `${field.label} 要在 0 到 1 之间`; return }
    policy[field.key] = value
  }
  await act(async () => {
    dataOf(await auth.api.PUT('/api/v1/runner/hosts/{host_id}/policy', { params: { header, path: { host_id: props.host!.id } }, body: { policy, version: props.host!.version } }))
  }, '已保存回收策略：下一轮检查（每分钟）起生效')
}
</script>

<template>
  <p v-if="error" role="alert" class="feedback">{{ error }}</p>
  <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
  <section class="panel runner-card" aria-label="容器服务器">
    <h2>容器服务器</h2>
    <div>
      <p class="form-help">用一台专用的 Linux 服务器（x86_64 或 ARM64，内核 5.6 以上）：接入包会装好锁定版本的 Docker、gVisor 和 runner-agent。runner-agent 主动连工作台的接入入口，服务器不用开入站端口。</p>
      <form class="runner-filters" aria-label="添加容器服务器" @submit.prevent="add">
        <label class="form-field"><span>名称</span><input v-model="name" maxlength="64" required /></label>
        <label class="form-field"><span>连工作台的入口</span><select v-model="ingress"><option value="intranet">内网入口</option><option value="public">外网入口（另发客户端证书）</option></select></label>
        <button type="submit" class="primary-button" :disabled="busy || !name.trim()">添加</button>
      </form>
    </div>
    <template v-if="host">
      <dl class="agent-facts">
        <div><dt>名称</dt><dd>{{ host.name }}</dd></div>
        <div><dt>状态</dt><dd>{{ HOST_STATUS[host.status] }}</dd></div>
        <div><dt>入口</dt><dd>{{ host.ingress === 'public' ? '外网入口' : '内网入口' }}</dd></div>
        <div><dt>凭证</dt><dd class="num">{{ host.token_prefix ? `${host.token_prefix}…` : '还没有签发' }}</dd></div>
        <div><dt>最近连接</dt><dd>{{ host.last_seen_at ? formatTime(host.last_seen_at) : '从未' }}</dd></div>
      </dl>
      <p class="form-help">推荐用下面的“通过 SSH 部署”在页面上完成。也可以手动：下载接入包，传到容器服务器上解压，以 root 运行 <code>install.sh</code>（装 Docker 29.9.1、gVisor release-20261019.0 并核对 sha512、启动 runner-agent）。升级 runner-agent 也是重新部署一次，容器与数据卷不受影响。</p>
      <div class="form-actions">
        <button type="button" class="secondary-button" :disabled="busy" @click="bundle">{{ host.last_seen_at ? '手动重新部署：下载新的接入包' : '手动部署：下载接入包' }}</button>
        <button type="button" class="secondary-button im-danger" :disabled="busy" @click="remove">移除容器服务器</button>
      </div>
    </template>
  </section>

  <RunnerSshDeploy v-if="host && host.backend !== 'kubernetes'" :key="host.id" :host-id="host.id" />

  <ModuleServerPanels v-if="host" place="runnerHost" :server-id="host.id" :server-name="host.name" />

  <form v-if="host" class="panel runner-card runner-form" aria-label="回收策略" @submit.prevent="savePolicy">
    <h2>回收策略</h2>
    <p class="form-help">显示的是生效值；清空一项就回到默认值。启动越慢停得越晚：空闲停止的时长取“类别默认”和“启动耗时 × 倍数”中大的那个。</p>
    <fieldset v-for="g in groups" :key="g.group" class="mail-scope">
      <legend>{{ g.group }}</legend>
      <div class="runner-filters">
        <label v-for="f in g.fields" :key="f.key" class="form-field"><span>{{ f.label }}</span><input v-model="values[f.key]" type="number" min="0" :step="f.step ?? 1" /></label>
      </div>
    </fieldset>
    <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy">保存回收策略</button><button type="button" class="secondary-button" @click="reset">还原</button></div>
  </form>
</template>
