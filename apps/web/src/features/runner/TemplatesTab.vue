<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { dataOf, type Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { formatTime } from '../time'
import { BUILD, LIFETIME, NETWORK, RUNTIME, USER, riskyChoices, seconds, useRunnerAction, useRunnerTemplates, type RunnerTemplate } from './api'

// 模板（RUNNER_PLAN 第 3、11 节）：谁建的（智能体 / 管理员）、镜像 digest、运行时、网络、构建状态与日志、
// 构建耗时、镜像大小、使用次数；管理员可以新建、修改（规格变了版本号加一并重新构建）、重新构建、停用。
// 智能体写的说明只当数据显示（untrusted）。
type Spec = Schema['RunnerSpecIn']
const auth = useAuthStore(), scope = auth.workspaceId!
const templates = useRunnerTemplates(scope)
const items = computed(() => templates.data.value?.items ?? [])
const { busy, error, notice, act, header } = useRunnerAction(scope)
const open = ref<string | null>(null)

const blank = () => ({ name: '', description: '', image: '', steps: '', start: '', runtime: 'gvisor' as Spec['runtime'], user: 'user' as Spec['user'],
  network: 'none' as Spec['network'], domains: '', memory_mb: '', cpus: '', disk_gb: '', env: '', secrets: '', persist: '', lifetime: 'session' as NonNullable<Spec['lifetime']>, version: null as number | null })
const form = reactive(blank())
const editing = ref<'new' | string | null>(null)
const lines = (text: string) => text.split('\n').map(s => s.trim()).filter(Boolean)
const words = (text: string) => text.split(/[\n,，\s]+/).map(s => s.trim()).filter(Boolean)

function start(template?: RunnerTemplate) {
  Object.assign(form, blank())
  if (template) {
    const s = template.spec
    Object.assign(form, { name: template.name, description: template.untrusted.description, image: s.image, steps: s.steps.join('\n'), start: s.start_command?.join(' ') ?? '',
      runtime: s.runtime, user: s.user, network: s.network, domains: s.domains.join('\n'), memory_mb: String(s.memory_mb), cpus: String(s.cpus), disk_gb: String(s.disk_gb),
      env: Object.entries(s.env).map(([k, v]) => `${k}=${v}`).join('\n'), secrets: s.secrets.join('\n'), persist: s.persist.join('\n'), lifetime: s.lifetime, version: template.version })
  }
  editing.value = template?.id ?? 'new'
}
function spec(): Spec | string {
  const env: Record<string, string> = {}
  for (const line of lines(form.env)) {
    const at = line.indexOf('=')
    if (at < 1) return `环境变量要写成 名称=值：${line}`
    env[line.slice(0, at).trim()] = line.slice(at + 1)
  }
  const number = (text: string) => text.trim() ? Number(text) : null
  return {
    image: form.image.trim(), steps: lines(form.steps), start_command: form.start.trim() || null, runtime: form.runtime, user: form.user, network: form.network,
    domains: form.network === 'allowlist' ? words(form.domains) : [], memory_mb: number(form.memory_mb), cpus: number(form.cpus), disk_gb: number(form.disk_gb),
    env, secrets: words(form.secrets), persist: lines(form.persist), lifetime: form.lifetime,
  }
}
async function save() {
  const body = spec()
  if (typeof body === 'string') { error.value = body; return }
  if (!/^[a-z0-9][a-z0-9._-]*$/.test(form.name)) { error.value = '名称只能是小写字母、数字、点、横线、下划线，以字母或数字开头。'; return }
  if (!body.image) { error.value = '要写基础镜像，例如 python:3.12-slim。'; return }
  const ok = await act(async () => {
    dataOf(await auth.api.PUT('/api/v1/runner/templates', { params: { header }, body: { name: form.name, description: form.description, spec: body, version: form.version } }))
  }, `已保存模板 ${form.name}：规格变了会重新构建`)
  if (ok) editing.value = null
}
async function rebuild(t: RunnerTemplate) {
  await act(async () => { dataOf(await auth.api.POST('/api/v1/runner/templates/{template_id}/rebuild', { params: { header, path: { template_id: t.id } } })) }, `已开始重新构建 ${t.name}`)
}
async function toggle(t: RunnerTemplate) {
  const status = t.status === 'active' ? 'disabled' : 'active'
  if (status === 'disabled' && !window.confirm(`停用模板 ${t.name}？已经建好的环境不受影响，但不能再用它新建。`)) return
  await act(async () => { dataOf(await auth.api.PUT('/api/v1/runner/templates/{template_id}/status', { params: { header, path: { template_id: t.id } }, body: { status, version: t.version } })) }, status === 'disabled' ? `已停用 ${t.name}` : `已启用 ${t.name}`)
}
</script>

<template>
  <div class="section-heading">
    <p class="form-help">模板一般由智能体按任务需要自己定义（runner_template_save），也可以在这里新建。安装步骤是 Dockerfile 里 FROM 之后的部分；构建只能经出口代理联网。</p>
    <button type="button" class="primary-button" :disabled="busy" @click="start()">新建模板</button>
  </div>
  <p v-if="error" role="alert" class="feedback">{{ error }}</p>
  <p v-if="notice" role="status" class="feedback">{{ notice }}</p>

  <form v-if="editing" class="panel runner-card runner-form" :aria-label="editing === 'new' ? '新建模板' : '修改模板'" @submit.prevent="save">
    <h2>{{ editing === 'new' ? '新建模板' : `修改模板 ${form.name}` }}</h2>
    <label class="form-field"><span>名称（小写字母、数字、点、横线、下划线）</span><input v-model="form.name" maxlength="64" :readonly="editing !== 'new'" required /></label>
    <label class="form-field"><span>说明</span><input v-model="form.description" maxlength="500" /></label>
    <label class="form-field"><span>基础镜像</span><input v-model="form.image" maxlength="512" placeholder="python:3.12-slim" required /></label>
    <label class="form-field"><span>安装步骤（每行一条，如 RUN pip install pandas）</span><textarea v-model="form.steps" rows="4" /></label>
    <label class="form-field"><span>启动命令（常驻进程，可以不填）</span><input v-model="form.start" maxlength="2000" /></label>
    <div class="runner-filters">
      <label class="form-field"><span>运行时</span><select v-model="form.runtime"><option v-for="(name, key) in RUNTIME" :key="key" :value="key">{{ name }}</option></select></label>
      <label class="form-field"><span>用户</span><select v-model="form.user"><option v-for="(name, key) in USER" :key="key" :value="key">{{ name }}</option></select></label>
      <label class="form-field"><span>网络</span><select v-model="form.network"><option v-for="(name, key) in NETWORK" :key="key" :value="key">{{ name }}</option></select></label>
      <label class="form-field"><span>默认类别</span><select v-model="form.lifetime"><option v-for="(name, key) in LIFETIME" :key="key" :value="key">{{ name }}</option></select></label>
    </div>
    <label v-if="form.network === 'allowlist'" class="form-field"><span>放行的域名（每行一个）</span><textarea v-model="form.domains" rows="2" /></label>
    <div class="runner-filters">
      <label class="form-field"><span>内存（MB）</span><input v-model="form.memory_mb" type="number" min="64" placeholder="2048" /></label>
      <label class="form-field"><span>CPU（核）</span><input v-model="form.cpus" type="number" min="0.1" step="0.1" placeholder="2" /></label>
      <label class="form-field"><span>磁盘（GB）</span><input v-model="form.disk_gb" type="number" min="1" placeholder="20" /></label>
    </div>
    <label class="form-field"><span>环境变量（名称=值，每行一个；不要放密钥）</span><textarea v-model="form.env" rows="2" /></label>
    <label class="form-field"><span>注入的密钥（密钥库里的名称）</span><input v-model="form.secrets" /></label>
    <label class="form-field"><span>归档时保存的目录（/workspace 下，每行一个；不填保存整个 /workspace）</span><textarea v-model="form.persist" rows="2" /></label>
    <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy">保存并构建</button><button type="button" class="secondary-button" @click="editing = null">取消</button></div>
  </form>

  <p v-if="templates.isPending.value" role="status">正在加载模板…</p>
  <p v-else-if="templates.error.value" role="alert">加载失败，请稍后重试。</p>
  <p v-else-if="!items.length" class="panel empty-state">还没有模板。</p>
  <ul v-else class="runner-list" aria-label="模板列表">
    <li v-for="t in items" :key="t.id">
      <article class="panel runner-card" :aria-label="`模板：${t.name}`">
        <div class="section-heading">
          <h3>{{ t.name }} <span class="badge">第 {{ t.revision }} 版</span> <span class="badge">{{ BUILD[t.build_status] }}</span><span v-if="t.status === 'disabled'" class="badge">已停用</span></h3>
          <div class="form-actions runner-actions">
            <button type="button" class="secondary-button" :disabled="busy" @click="start(t)">修改</button>
            <button type="button" class="secondary-button" :disabled="busy || t.build_status === 'building'" @click="rebuild(t)">重新构建</button>
            <button type="button" class="secondary-button" :disabled="busy" @click="toggle(t)">{{ t.status === 'active' ? '停用' : '启用' }}</button>
          </div>
        </div>
        <p v-if="t.untrusted.description" class="preserve-text">{{ t.untrusted.description }}</p>
        <p v-if="riskyChoices(t.spec).length" class="form-help agent-notice" role="note">非默认选项（隔离更弱）：{{ riskyChoices(t.spec).join('、') }}。</p>
        <dl class="agent-facts">
          <div><dt>建立者</dt><dd>{{ t.owner }}</dd></div>
          <div><dt>基础镜像</dt><dd>{{ t.spec.image }}</dd></div>
          <div><dt>镜像 digest</dt><dd class="num">{{ t.image_digest ?? '—' }}</dd></div>
          <div><dt>运行时 / 用户 / 网络</dt><dd>{{ RUNTIME[t.spec.runtime] }} / {{ USER[t.spec.user] }} / {{ NETWORK[t.spec.network] }}</dd></div>
          <div><dt>构建耗时 / 镜像大小</dt><dd>{{ seconds(t.build_seconds) }} / {{ t.image_mb === null ? '—' : `${t.image_mb} MB` }}</dd></div>
          <div><dt>使用次数</dt><dd>{{ t.use_count }}<template v-if="t.last_used_at">，最近 {{ formatTime(t.last_used_at) }}</template></dd></div>
        </dl>
        <p v-if="t.build_error" role="alert" class="feedback">构建失败：{{ t.build_error }}</p>
        <details v-if="t.build_log" :open="open === t.id" @toggle="open = ($event.target as HTMLDetailsElement).open ? t.id : null">
          <summary>构建日志（结尾，已脱敏）</summary>
          <pre class="runner-output">{{ t.build_log }}</pre>
        </details>
        <details v-if="t.spec.steps.length"><summary>安装步骤（{{ t.spec.steps.length }} 条）</summary><pre class="runner-output">{{ t.spec.steps.join('\n') }}</pre></details>
      </article>
    </li>
  </ul>
</template>
