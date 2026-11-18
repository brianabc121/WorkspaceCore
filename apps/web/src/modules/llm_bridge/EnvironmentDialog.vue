<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, useId } from 'vue'
import { FolderPlus, X } from '@lucide/vue'
import { PLATFORM_MARK } from './chat/chatApi'
import { PLATFORMS, SANDBOX, bridgeError, platformName, type Environment, type EnvironmentBody, type LlmBridgeApi, type Platform } from './api'

// 新增 / 修改开发环境的弹窗（T-0289，效果图“新增开发环境”）：基本信息（名称、运行平台）、代码仓库（可选）、
// 运行权限（访问权限、允许联网、启用环境）、说明（可选）。和原来的表单提交同样的内容、同样的校验。
// 点外面不关（免得填了一半丢掉），Esc 和“取消”关；Tab 只在弹窗里转。
const props = defineProps<{ api: LlmBridgeApi; bridgeId: string; env: Environment | null; platforms: readonly { id: Platform; name: string }[] }>()
const emit = defineEmits<{ close: []; saved: [message: string]; remove: [env: Environment] }>()

const SANDBOX_HELP: Record<EnvironmentBody['sandbox'], string> = {
  read_only: '允许查看文件，不修改内容。',
  workspace_write: '可以改工作目录里的文件，不能执行命令。',
  exec: '可以改文件、执行命令（在 MCP 管理器的容器里）。',
}
const SANDBOXES = Object.entries(SANDBOX) as [EnvironmentBody['sandbox'], string][]
const editing = computed(() => !!props.env)
const choices = computed(() => (editing.value ? PLATFORMS : props.platforms))
const form = reactive({
  platform: (props.env?.platform ?? props.platforms[0]?.id ?? 'codex') as EnvironmentBody['platform'],
  name: props.env?.name ?? '', repo_url: props.env?.repo_url ?? '', branch: props.env?.branch ?? '',
  sandbox: (props.env?.sandbox ?? 'read_only') as EnvironmentBody['sandbox'], network: props.env?.network ?? false,
  description: props.env?.description ?? '', enabled: props.env?.enabled ?? true,
})
const execWarning = computed(() => form.sandbox === 'exec' && (form.platform === 'claude' || form.platform === 'workbuddy'))
const error = ref(''), busy = ref(false)

async function submit() {
  const name = form.name.trim(), repo = form.repo_url.trim(), branch = form.branch.trim()
  if (!name) { error.value = '请填写环境的名称。'; return }
  if (repo && !/^https:\/\/\S+$/.test(repo)) { error.value = '代码仓库地址只支持 https:// 开头的地址。'; return }
  const body: EnvironmentBody = { platform: form.platform, mode: 'dev', name, repo_url: repo || null, branch: branch || null, sandbox: form.sandbox, network: form.network, description: form.description.trim(), enabled: form.enabled }
  busy.value = true; error.value = ''
  try {
    await props.api.saveEnvironment(props.bridgeId, props.env?.id ?? null, body)
    emit('saved', editing.value ? `已保存环境「${name}」。` : `已新增环境「${name}」。`)
  } catch (e) { error.value = bridgeError(e) } finally { busy.value = false }
}

// 弹窗的键盘：Esc 关，Tab 只在弹窗里转（和 ModalPanel 一样）
const panel = ref<HTMLElement | null>(null), titleId = useId()
const previous = document.activeElement as HTMLElement | null
function keys(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.stopPropagation(); if (!busy.value) emit('close'); return }
  if (event.key !== 'Tab' || !panel.value) return
  const items = [...panel.value.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled)')]
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
        <div><h2 :id="titleId">{{ editing ? '修改开发环境' : '新增开发环境' }}</h2><p>配置平台、代码仓库与运行权限</p></div>
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

        <section class="lb-dialog-sec" aria-label="代码仓库">
          <h3>代码仓库<span class="lb-optional">可选</span></h3>
          <div class="lb-dialog-row">
            <div class="form-field"><label for="lb-env-repo">仓库地址</label><input id="lb-env-repo" v-model="form.repo_url" maxlength="500" inputmode="url" placeholder="https://github.com/owner/repo" /></div>
            <div class="form-field lb-dialog-branch"><label for="lb-env-branch">分支</label><input id="lb-env-branch" v-model="form.branch" maxlength="200" placeholder="main" /></div>
          </div>
          <p class="form-help">支持 HTTPS 地址，也可留空（空的工作目录）。</p>
        </section>

        <section class="lb-dialog-sec" aria-label="运行权限">
          <h3>运行权限</h3>
          <div class="lb-set-row lb-dialog-access">
            <label for="lb-env-sandbox">访问权限</label>
            <div class="lb-dialog-select">
              <select id="lb-env-sandbox" v-model="form.sandbox"><option v-for="[id, name] in SANDBOXES" :key="id" :value="id">{{ name }}</option></select>
              <p class="form-help">{{ SANDBOX_HELP[form.sandbox] }}</p>
            </div>
          </div>
          <p v-if="execWarning" role="note" class="lb-warn is-red">注意：{{ platformName(form.platform) }} 的命令以容器里的普通用户身份运行，没有操作系统级沙箱，能读到这个 MCP 管理器数据卷里的其他内容。</p>
          <label class="lb-set-row lb-set-switch-row">
            <span class="lb-set-text"><strong>允许联网</strong><span>允许环境访问外部网络</span></span>
            <input v-model="form.network" type="checkbox" role="switch" class="lb-switch-input" />
          </label>
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
          <p>仅添加到当前 MCP 管理器</p>
          <button v-if="env" type="button" class="secondary-button lb-danger" :disabled="busy" @click="emit('remove', env)">删除环境</button>
          <button type="button" class="secondary-button" :disabled="busy" @click="emit('close')">取消</button>
          <button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在保存…' : editing ? '保存' : '创建环境' }}</button>
        </footer>
      </form>
    </section>
  </div>
</template>
