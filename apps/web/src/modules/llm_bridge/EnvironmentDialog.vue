<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, toRef, useId, watch } from 'vue'
import { FolderPlus, X } from '@lucide/vue'
import { PLATFORM_MARK } from './chat/chatApi'
import {
  CONTAINER_STATE, IDLE_STOP, PLATFORMS, REMOTE_NETWORK, REMOTE_NETWORK_NOTE, REMOTE_PRESET, SANDBOX, bridgeError, newDeployRequestId, platformName, resourceText,
  type Environment, type EnvironmentBody, type LlmBridgeApi, type Platform, type RemoteContainerBody, type RemoteNetwork,
} from './api'

// 新增 / 修改开发环境的弹窗（T-0289，效果图“新增开发环境”）：基本信息（名称、运行平台）、运行位置、代码仓库（可选）、
// 运行权限（访问权限、允许联网、启用环境）、说明（可选）。点外面不关（免得填了一半丢掉），Esc 和“取消”关；Tab 只在弹窗里转。
// 运行位置（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 10.1）：MCP 管理器自己的容器，或者容器服务器上的远程开发容器
// （新建一个，或者用这个管理器已有的）。运行位置和平台一样，建好后不能改。
const props = defineProps<{ api: LlmBridgeApi; bridgeId: string; env: Environment | null; platforms: readonly { id: Platform; name: string }[] }>()
const emit = defineEmits<{ close: []; saved: [message: string]; remove: [env: Environment] }>()

const SANDBOX_HELP: Record<EnvironmentBody['sandbox'], string> = {
  read_only: '允许查看文件，不修改内容。',
  workspace_write: '可以改工作目录里的文件，不能执行命令。',
  exec: '可以改文件、执行命令（在 MCP 管理器的容器里）。',
}
const REMOTE_EXEC_HELP = '可以改文件、执行命令（在远程容器里，容器本身是沙箱）。'
const SANDBOXES = Object.entries(SANDBOX) as [EnvironmentBody['sandbox'], string][]
const NETWORKS = Object.entries(REMOTE_NETWORK) as [RemoteNetwork, { name: string; help: string }][]
const PRESETS = Object.entries(REMOTE_PRESET) as ['small' | 'medium' | 'large', string][]
const editing = computed(() => !!props.env)
const choices = computed(() => (editing.value ? PLATFORMS : props.platforms))
const form = reactive({
  platform: (props.env?.platform ?? props.platforms[0]?.id ?? 'codex') as EnvironmentBody['platform'],
  name: props.env?.name ?? '', repo_url: props.env?.repo_url ?? '', branch: props.env?.branch ?? '',
  sandbox: (props.env?.sandbox ?? 'read_only') as EnvironmentBody['sandbox'], network: props.env?.network ?? false,
  description: props.env?.description ?? '', enabled: props.env?.enabled ?? true,
  // 这个环境的单回合时限（分钟，docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md 5.7）：不填用管理器的设置
  timeout: (props.env?.turn_timeout_seconds ? String(Math.round(props.env.turn_timeout_seconds / 60)) : '') as string | number,
})

// ---- 运行位置 ----
const location = ref<'local' | 'remote'>(props.env?.location ?? 'local')
const remote = computed(() => location.value === 'remote')
const bridgeIdRef = toRef(() => props.bridgeId)
const containers = props.api.useContainers(bridgeIdRef)
const usable = computed(() => (containers.data.value ?? []).filter(c => !['gone', 'deleting', 'failed'].includes(c.state)))
const targets = props.api.useRemoteTargets(computed(() => remote.value && !editing.value))
const hosts = computed(() => targets.data.value?.hosts ?? [])
const presets = computed(() => targets.data.value?.presets)
const pick = ref<'new' | 'existing'>('new')
const containerId = ref('')
const advanced = ref(false)
const nc = reactive({
  name: '', host_id: '', image_kind: 'builtin' as RemoteContainerBody['image_kind'], image: '',
  preset: 'medium' as 'small' | 'medium' | 'large' | 'custom', cpus: 2 as number | string, memory_gb: 4 as number | string, disk_gb: 20 as number | string,
  runtime: 'gvisor' as 'gvisor' | 'runc', network: 'all' as RemoteNetwork, domains: '', secrets: '', idle_stop_minutes: 30 as 0 | 30 | 120,
})
// 有可用的远程容器时默认用已有的；能选的服务器只有一台时直接选上
watch(usable, list => { if (!editing.value && list.length && !containerId.value) { pick.value = 'existing'; containerId.value = list[0]!.id } }, { immediate: true })
watch(hosts, list => { const ok = list.filter(h => h.available); if (!nc.host_id && ok.length === 1) nc.host_id = ok[0]!.id }, { immediate: true })
// 远程容器本身是沙箱：新建时访问权限默认“可执行命令”；Codex 在远程容器里只有这一档（核验记录 V2）
let sandboxTouched = !!props.env
watch(location, where => { if (!editing.value && where === 'remote' && !sandboxTouched) form.sandbox = 'exec' })
const codexRemote = computed(() => remote.value && form.platform === 'codex')
watch(codexRemote, only => { if (only) form.sandbox = 'exec' }, { immediate: true })
const execWarning = computed(() => !remote.value && form.sandbox === 'exec' && (form.platform === 'claude' || form.platform === 'workbuddy'))
const sandboxHelp = computed(() => (remote.value && form.sandbox === 'exec' ? REMOTE_EXEC_HELP : SANDBOX_HELP[form.sandbox]))
const chosenHost = computed(() => hosts.value.find(h => h.id === nc.host_id) ?? null)
const resources = computed(() => {
  if (nc.preset !== 'custom') return presets.value?.[nc.preset] ?? null
  return { cpus: Number(nc.cpus), memory_mb: Math.round(Number(nc.memory_gb) * 1024), disk_gb: Number(nc.disk_gb) }
})
const footNote = computed(() => {
  if (!remote.value) return '仅添加到当前 MCP 管理器'
  if (editing.value) return `运行在远程容器「${props.env?.remote_label ?? '—'}」里`
  if (pick.value === 'existing') return '用这个管理器已有的远程容器；可以在容器服务器页面查看。'
  const disk = resources.value?.disk_gb
  return `会在容器服务器「${chosenHost.value?.name ?? '…'}」上新建一个容器${disk ? `（约 ${disk} GB 磁盘）` : ''}；可以在容器服务器页面查看。`
})
const list = (text: string) => text.split(/[\s,，、]+/).map(s => s.trim()).filter(Boolean)
const error = ref(''), busy = ref(false)
// 新增的幂等号：这个弹窗里重试（例如网络断了再点一次）不会多建一个环境或远程容器
const requestId = newDeployRequestId()

function newContainer(envName: string): RemoteContainerBody | string {
  const name = (nc.name.trim() || `${envName} 容器`).slice(0, 60)
  if (!nc.host_id) return hosts.value.length ? '请选一台容器服务器。' : '还没有能用的容器服务器：先到“容器服务器”页面添加。'
  if (chosenHost.value && !chosenHost.value.available) return `这台容器服务器现在不能用：${chosenHost.value.reason ?? ''}`
  const image = nc.image.trim()
  if (nc.image_kind !== 'builtin' && !image) return nc.image_kind === 'template' ? '请填模板名称。' : '请填镜像名称。'
  const body: RemoteContainerBody = {
    name, host_id: nc.host_id, image_kind: nc.image_kind, image: nc.image_kind === 'builtin' ? null : image, preset: nc.preset,
    runtime: nc.runtime, network: nc.network, domains: list(nc.domains), secrets: list(nc.secrets), idle_stop_minutes: nc.idle_stop_minutes,
  }
  if (nc.preset === 'custom') {
    const r = resources.value!
    if (!(r.cpus >= 0.5 && r.cpus <= 32) || !(r.memory_mb >= 512 && r.memory_mb <= 131072) || !(Number.isInteger(r.disk_gb) && r.disk_gb >= 5 && r.disk_gb <= 1000)) return '自定义资源：CPU 0.5–32 核，内存 0.5–128 GB，磁盘 5–1000 GB（整数）。'
    body.resources = r
  }
  if ((body.domains ?? []).length > 50) return '另加的域名最多 50 个。'
  return body
}

async function submit() {
  const name = form.name.trim(), repo = form.repo_url.trim(), branch = form.branch.trim()
  if (!name) { error.value = '请填写环境的名称。'; return }
  if (repo && !/^https:\/\/\S+$/.test(repo)) { error.value = '代码仓库地址只支持 https:// 开头的地址。'; return }
  const minutes = String(form.timeout ?? '').trim() ? Number(form.timeout) : null
  if (minutes != null && (!Number.isInteger(minutes) || minutes < 1 || minutes > 360)) { error.value = '单回合时限要填 1–360 分钟，或者留空用管理器的设置。'; return }
  if (codexRemote.value && form.sandbox !== 'exec') { error.value = '远程容器里的 Codex 只有“可执行命令”这一档。'; return }
  const body: EnvironmentBody = {
    platform: form.platform, mode: 'dev', name, repo_url: repo || null, branch: branch || null, sandbox: form.sandbox,
    network: remote.value ? false : form.network, description: form.description.trim(), enabled: form.enabled,
    turn_timeout_seconds: minutes == null ? null : Math.max(30, minutes * 60), location: location.value,
  }
  if (!editing.value) body.request_id = requestId
  if (remote.value) {
    if (editing.value) body.remote_container_id = props.env?.remote_container ?? null
    else if (pick.value === 'existing') {
      if (!containerId.value) { error.value = '请选一个远程容器，或者新建一个。'; return }
      body.remote_container_id = containerId.value
    } else {
      const made = newContainer(name)
      if (typeof made === 'string') { error.value = made; return }
      body.new_container = made
    }
  }
  busy.value = true; error.value = ''
  try {
    await props.api.saveEnvironment(props.bridgeId, props.env?.id ?? null, body)
    const extra = remote.value && !editing.value && pick.value === 'new' ? '远程容器正在构建镜像，第一次要几分钟，进度在环境列表里。' : ''
    emit('saved', `${editing.value ? `已保存环境「${name}」。` : `已新增环境「${name}」。`}${extra}`)
  } catch (e) { error.value = bridgeError(e) } finally { busy.value = false }
}

// 弹窗的键盘：Esc 关，Tab 只在弹窗里转（和 ModalPanel 一样）
const panel = ref<HTMLElement | null>(null), titleId = useId()
const previous = document.activeElement as HTMLElement | null
function keys(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.stopPropagation(); if (!busy.value) emit('close'); return }
  if (event.key !== 'Tab' || !panel.value) return
  const items = [...panel.value.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href]')]
  const first = items[0], last = items.at(-1)
  if (!panel.value.contains(document.activeElement)) { event.preventDefault(); first?.focus() }
  else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
onMounted(async () => { document.addEventListener('keydown', keys); await nextTick(); panel.value?.querySelector<HTMLElement>('#lb-env-name')?.focus() })
onUnmounted(() => { document.removeEventListener('keydown', keys); previous?.focus?.() })
</script>

<template>
  <div class="lb-dialog-backdrop">
    <section ref="panel" class="lb-dialog" role="dialog" aria-modal="true" :aria-labelledby="titleId">
      <header class="lb-dialog-head">
        <span class="lb-set-icon" aria-hidden="true"><FolderPlus /></span>
        <div><h2 :id="titleId">{{ editing ? '修改开发环境' : '新增开发环境' }}</h2><p>配置平台、运行位置、代码仓库与运行权限</p></div>
        <button type="button" class="lb-icon-btn" aria-label="关闭" :disabled="busy" @click="emit('close')"><X aria-hidden="true" /></button>
      </header>
      <form class="lb-dialog-body" novalidate :aria-label="editing ? '修改开发环境' : '新增开发环境'" @submit.prevent="submit">
        <p v-if="error" role="alert" class="lb-warn is-red">{{ error }}</p>
        <section class="lb-dialog-sec" aria-label="基本信息">
          <h3>基本信息</h3>
          <div class="form-field">
            <label for="lb-env-name">环境名称<span class="lb-required" aria-hidden="true">*</span></label>
            <input id="lb-env-name" v-model="form.name" maxlength="60" required placeholder="例如：WorkspaceCore 开发环境" />
          </div>
          <fieldset class="lb-plat-cards" :disabled="editing">
            <legend>运行平台</legend>
            <label v-for="p in choices" :key="p.id" class="lb-plat-card">
              <input v-model="form.platform" type="radio" name="lb-env-platform" :value="p.id" />
              <span class="lb-chip-mark lb-plat-mark" :data-platform="p.id" :data-mark="PLATFORM_MARK[p.id]" aria-hidden="true" />
              <span>{{ p.name }}</span>
            </label>
          </fieldset>
          <p class="form-help">{{ editing ? '建好后不能更换平台。' : '创建后不能更换平台；只能用这个管理器控制的平台。' }}</p>
        </section>

        <section class="lb-dialog-sec" aria-label="运行位置">
          <h3>运行位置</h3>
          <fieldset class="lb-place-cards" :disabled="editing">
            <legend class="sr-only">运行位置</legend>
            <label class="lb-place-card">
              <input v-model="location" type="radio" name="lb-env-location" value="local" />
              <strong>MCP 管理器容器</strong>
              <span>在这个管理器自己的容器里运行。适合读代码、改文件；不能装系统软件。</span>
            </label>
            <label class="lb-place-card">
              <input v-model="location" type="radio" name="lb-env-location" value="remote" />
              <strong>云端远程容器</strong>
              <span>在容器服务器上运行一个专用容器。可以装软件、跑测试、长时间运行；会出现在容器服务器页面。</span>
            </label>
          </fieldset>
          <p v-if="editing" class="form-help">运行位置建好后不能改{{ remote && env?.remote_label ? `：远程容器「${env.remote_label}」` : '' }}。</p>
        </section>

        <section v-if="remote && !editing" class="lb-dialog-sec" aria-label="远程容器">
          <h3>远程容器</h3>
          <div class="lb-set-row lb-dialog-access">
            <label for="lb-remote-pick">容器</label>
            <div class="lb-dialog-select">
              <select id="lb-remote-pick" v-model="pick">
                <option value="new">新建一个远程容器</option>
                <option value="existing" :disabled="!usable.length">用这个管理器已有的远程容器{{ usable.length ? '' : '（还没有）' }}</option>
              </select>
            </div>
          </div>
          <div v-if="pick === 'existing'" class="lb-set-row lb-dialog-access">
            <label for="lb-remote-existing">已有的容器</label>
            <div class="lb-dialog-select">
              <select id="lb-remote-existing" v-model="containerId">
                <option v-for="c in usable" :key="c.id" :value="c.id">{{ c.name }} · {{ c.host_name ?? '—' }} · {{ CONTAINER_STATE[c.state].text }}</option>
              </select>
              <p class="form-help">几个环境共用一个容器：装好的软件和缓存共用，每个会话仍然有自己的工作目录。</p>
            </div>
          </div>
          <template v-else>
            <p v-if="targets.isPending.value" role="status" class="form-help">正在读取容器服务器…</p>
            <p v-else-if="targets.error.value" role="alert" class="lb-warn is-red">{{ bridgeError(targets.error.value) }}</p>
            <p v-else-if="!hosts.length" role="note" class="lb-warn">还没有容器服务器：先到 <RouterLink to="/runner" class="asv-link">容器服务器</RouterLink> 页面添加一台。</p>
            <template v-else>
              <div class="lb-set-row lb-dialog-access">
                <label for="lb-remote-host">容器服务器</label>
                <div class="lb-dialog-select">
                  <select id="lb-remote-host" v-model="nc.host_id">
                    <option value="" disabled>选一台容器服务器</option>
                    <option v-for="h in hosts" :key="h.id" :value="h.id" :disabled="!h.available">{{ h.name }}{{ h.available ? (h.cpus ? `（${h.cpus} 核${h.memory_mb ? ` · ${Math.round(h.memory_mb / 1024)} GB` : ''}${h.disk_gb != null ? ` · 剩余磁盘 ${h.disk_gb} GB` : ''}）` : '') : `（${h.reason ?? '不能用'}）` }}</option>
                  </select>
                  <p v-if="hosts.every(h => !h.available)" class="form-help">现在没有能用的容器服务器：{{ hosts[0]?.reason }}</p>
                </div>
              </div>
              <div class="form-field">
                <label for="lb-remote-name">容器名称</label>
                <input id="lb-remote-name" v-model="nc.name" maxlength="60" :placeholder="`${form.name.trim() || '开发环境'} 容器`" />
              </div>
              <div class="lb-set-row lb-dialog-access">
                <label for="lb-remote-image">镜像</label>
                <div class="lb-dialog-select">
                  <select id="lb-remote-image" v-model="nc.image_kind">
                    <option value="builtin">内置“大模型开发环境”（推荐）</option>
                    <option value="template">容器服务器上的模板</option>
                    <option value="custom">自定义镜像</option>
                  </select>
                  <input v-if="nc.image_kind !== 'builtin'" v-model="nc.image" :aria-label="nc.image_kind === 'template' ? '模板名称' : '镜像名称'" maxlength="300" :placeholder="nc.image_kind === 'template' ? '模板名称' : '例如 python:3.12-slim'" />
                  <p class="form-help">{{ nc.image_kind === 'builtin' ? 'Node 22、Python 3、Git、构建工具，三家命令行和 MCP 管理器同版本。第一次构建要几分钟。' : '在它上面加一层命令行：要求是 glibc 的 Linux 镜像，有 /bin/sh。缺 timeout、find 这类基础命令时，构建时会用镜像自带的包管理器自动补装。' }}</p>
                </div>
              </div>
              <fieldset class="lb-place-cards lb-preset-cards">
                <legend>资源</legend>
                <label v-for="[id, label] in PRESETS" :key="id" class="lb-place-card">
                  <input v-model="nc.preset" type="radio" name="lb-remote-preset" :value="id" />
                  <strong>{{ label }}</strong>
                  <span v-if="presets?.[id]">{{ resourceText(presets[id]!) }}</span>
                </label>
              </fieldset>
              <div class="lb-set-row lb-dialog-access">
                <label for="lb-remote-network">网络</label>
                <div class="lb-dialog-select">
                  <select id="lb-remote-network" v-model="nc.network"><option v-for="[id, n] in NETWORKS" :key="id" :value="id">{{ n.name }}</option></select>
                  <p class="form-help">{{ REMOTE_NETWORK[nc.network].help }}</p>
                  <p class="form-help">{{ REMOTE_NETWORK_NOTE }}</p>
                  <input v-if="nc.network !== 'all'" v-model="nc.domains" aria-label="另加的域名" maxlength="4000" placeholder="另加的域名，空格或逗号分开，例如 *.example.com" />
                </div>
              </div>
              <div class="lb-set-row lb-dialog-access">
                <label for="lb-remote-idle">空闲停止</label>
                <div class="lb-dialog-select">
                  <select id="lb-remote-idle" v-model.number="nc.idle_stop_minutes"><option v-for="o in IDLE_STOP" :key="o.minutes" :value="o.minutes">{{ o.name }}</option></select>
                  <p class="form-help">停止后数据还在，下一次发消息时自动启动（几秒）。不会自动归档。</p>
                </div>
              </div>
              <details class="lb-remote-advanced" :open="advanced" @toggle="advanced = ($event.target as HTMLDetailsElement).open">
                <summary>高级</summary>
                <div class="lb-set-row lb-dialog-access">
                  <label for="lb-remote-runtime">隔离</label>
                  <div class="lb-dialog-select">
                    <select id="lb-remote-runtime" v-model="nc.runtime"><option value="gvisor">gVisor（默认）</option><option value="runc">普通容器（隔离较弱）</option></select>
                    <p class="form-help">{{ nc.runtime === 'runc' ? '普通容器兼容性更好，但和容器服务器共用内核，隔离较弱；会通知管理员。' : 'gVisor 是用户态内核，隔离最强；个别程序不兼容时再换普通容器。' }}</p>
                  </div>
                </div>
                <label class="lb-set-row lb-set-switch-row">
                  <span class="lb-set-text"><strong>自定义资源</strong><span>不用上面的预设</span></span>
                  <input type="checkbox" role="switch" class="lb-switch-input" :checked="nc.preset === 'custom'" @change="nc.preset = ($event.target as HTMLInputElement).checked ? 'custom' : 'medium'" />
                </label>
                <div v-if="nc.preset === 'custom'" class="lb-remote-resources">
                  <div class="form-field"><label for="lb-remote-cpus">CPU（核）</label><input id="lb-remote-cpus" v-model="nc.cpus" type="number" step="0.5" min="0.5" max="32" /></div>
                  <div class="form-field"><label for="lb-remote-mem">内存（GB）</label><input id="lb-remote-mem" v-model="nc.memory_gb" type="number" step="0.5" min="0.5" max="128" /></div>
                  <div class="form-field"><label for="lb-remote-disk">磁盘（GB）</label><input id="lb-remote-disk" v-model="nc.disk_gb" type="number" step="1" min="5" max="1000" /></div>
                </div>
                <div class="form-field">
                  <label for="lb-remote-secrets">注入的密钥</label>
                  <input id="lb-remote-secrets" v-model="nc.secrets" maxlength="2000" placeholder="工作台密钥库里的密钥名称，例如 GIT_TOKEN" />
                  <p class="form-help">放进容器的 /run/secrets/，容器里的程序能读到：只给专用、权限小的密钥。</p>
                </div>
              </details>
            </template>
          </template>
        </section>

        <section class="lb-dialog-sec" aria-label="代码仓库">
          <h3>代码仓库<span class="lb-optional">可选</span></h3>
          <div class="lb-dialog-row">
            <div class="form-field"><label for="lb-env-repo">仓库地址</label><input id="lb-env-repo" v-model="form.repo_url" maxlength="500" inputmode="url" placeholder="https://github.com/owner/repo" /></div>
            <div class="form-field lb-dialog-branch"><label for="lb-env-branch">分支</label><input id="lb-env-branch" v-model="form.branch" maxlength="200" placeholder="main" /></div>
          </div>
          <p class="form-help">支持 HTTPS 地址，也可留空（空的工作目录）。{{ remote ? '远程容器只放行网络时，仓库所在的主机会自动加进放行名单。' : '' }}</p>
        </section>

        <section class="lb-dialog-sec" aria-label="运行权限">
          <h3>运行权限</h3>
          <div class="lb-set-row lb-dialog-access">
            <label for="lb-env-sandbox">访问权限</label>
            <div class="lb-dialog-select">
              <select id="lb-env-sandbox" v-model="form.sandbox" @change="sandboxTouched = true"><option v-for="[id, name] in SANDBOXES" :key="id" :value="id" :disabled="codexRemote && id !== 'exec'">{{ name }}</option></select>
              <p class="form-help">{{ sandboxHelp }}</p>
              <p v-if="codexRemote" class="form-help">远程容器里的 Codex 只有“可执行命令”这一档：Codex 自己的沙箱在容器里用不了，容器本身就是沙箱。</p>
            </div>
          </div>
          <p v-if="execWarning" role="note" class="lb-warn is-red">注意：{{ platformName(form.platform) }} 的命令以容器里的普通用户身份运行，没有操作系统级沙箱，能读到这个 MCP 管理器数据卷里的其他内容。</p>
          <p v-if="remote" role="note" class="lb-warn">容器里执行的命令能读到这个平台在容器里的登录凭据：建议用专用的 API 密钥，网络只放行需要的地址。</p>
          <label v-if="!remote" class="lb-set-row lb-set-switch-row">
            <span class="lb-set-text"><strong>允许联网</strong><span>允许环境访问外部网络</span></span>
            <input v-model="form.network" type="checkbox" role="switch" class="lb-switch-input" />
          </label>
          <div class="lb-set-row lb-dialog-access">
            <label for="lb-env-timeout">单回合时限</label>
            <div class="lb-dialog-select">
              <div class="lb-unit-input"><input id="lb-env-timeout" v-model="form.timeout" type="number" inputmode="numeric" step="1" min="1" max="360" placeholder="跟随管理器" /><span aria-hidden="true">分钟</span></div>
              <p class="form-help">这个环境的一个回合最长多久（1–360 分钟）；留空用“运行设置”里的。跑很久的命令建议交给后台作业。</p>
            </div>
          </div>
          <label class="lb-set-row lb-set-switch-row">
            <span class="lb-set-text"><strong>启用环境</strong><span>{{ editing ? '停用后不能再用它新建会话' : '创建后可用于新建会话' }}</span></span>
            <input v-model="form.enabled" type="checkbox" role="switch" class="lb-switch-input" />
          </label>
        </section>

        <section class="lb-dialog-sec" aria-label="说明">
          <div class="form-field">
            <label for="lb-env-desc">说明<span class="lb-optional">可选</span></label>
            <textarea id="lb-env-desc" v-model="form.description" rows="2" maxlength="300" placeholder="简单描述这个环境的用途" />
          </div>
        </section>

        <footer class="lb-dialog-foot">
          <p>{{ footNote }}</p>
          <button v-if="env" type="button" class="secondary-button lb-danger" :disabled="busy" @click="emit('remove', env)">删除环境</button>
          <button type="button" class="secondary-button" :disabled="busy" @click="emit('close')">取消</button>
          <button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在保存…' : editing ? '保存' : '创建环境' }}</button>
        </footer>
      </form>
    </section>
  </div>
</template>
