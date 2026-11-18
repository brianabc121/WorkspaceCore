<script setup lang="ts">
import { computed, reactive, ref, toRef, watch } from 'vue'
import { Plus } from '@lucide/vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { CONTAINER_STATE, PLATFORMS, SANDBOX, bridgeError, platformName, type Bridge, type BridgeStatus, type Environment, type LlmBridgeApi } from './api'
import BridgeWorkFiles from './BridgeWorkFiles.vue'
import EnvironmentDialog from './EnvironmentDialog.vue'
import RemoteContainerBar from './RemoteContainerBar.vue'
import WorkTreeNode from './WorkTreeNode.vue'
import { defaultSession, rootName } from './workFiles'

// 开发环境：智能体在哪个代码仓库、用什么权限干活。Codex / Claude / CodeBuddy 都有开发模式；
// 用法固定是“开发”（云端通道目前只是占位）。新增时只能选这个 MCP 管理器控制的平台（状态还没读到时不限制，由后端把关）。
// 按效果图（T-0289）：左边是环境列表和文件夹树，中间是选中环境的一个会话的工作目录，右边预览文件；
// 新增、修改环境在弹窗里。文件来自会话的工作目录（环境本身只是配置，没有常驻的目录）。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge; status?: BridgeStatus; canWrite: boolean }>()
const bridgeId = toRef(() => props.bridge.id)
const environments = props.api.useEnvironments(bridgeId)
const list = computed(() => environments.data.value ?? [])
const DEV_PLATFORMS = computed(() => PLATFORMS.filter(p => !props.status?.reachable || props.status.platforms.includes(p.id)))

// 选中的环境（默认第一个）、它的哪个会话（默认最近的、还能浏览的）、当前文件夹
const selectedId = ref<string | null>(null)
watch(list, items => { if (!items.some(e => e.id === selectedId.value)) selectedId.value = items[0]?.id ?? null }, { immediate: true })
const selected = computed(() => list.value.find(e => e.id === selectedId.value) ?? null)
// 云端远程容器（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 10.2）：远程环境标出在哪台服务器、容器的状态；
// 构建 / 创建中的容器每 5 秒刷新（api.useContainers）。读不到容器列表时只是不显示状态
const containers = props.api.useContainers(bridgeId)
const containerOf = (env: Environment) => (env.location === 'remote' ? (containers.data.value ?? []).find(c => c.id === env.remote_container) ?? null : null)
const selectedContainer = computed(() => (selected.value ? containerOf(selected.value) : null))
function placeText(env: Environment) {
  if (env.location !== 'remote') return ''
  const c = containerOf(env)
  return `云端 · ${c?.host_name ?? env.remote_label ?? '远程容器'}`
}
const sessions = props.api.useEnvSessions(bridgeId, selectedId)
const sessionList = computed(() => sessions.data.value ?? [])
const session = ref<string | null>(null), path = ref('')
watch(sessionList, items => { if (!items.some(s => s.session_id === session.value)) session.value = defaultSession(items) }, { immediate: true })
watch([selectedId, session], () => { path.value = '' })
const browsable = computed(() => !!sessionList.value.find(s => s.session_id === session.value)?.browsable)

const dialog = reactive({ open: false, env: null as Environment | null })
function openDialog(env?: Environment) { Object.assign(dialog, { open: true, env: env ?? null }); error.value = notice.value = '' }
const error = ref(''), notice = ref(''), busy = ref(false)
function saved(message: string) { Object.assign(dialog, { open: false, env: null }); notice.value = message; error.value = '' }
async function remove(env: Environment) {
  if (busy.value || !window.confirm(`删除开发环境「${env.name}」？已经用它建好的会话不受影响，只是不能再用它新建会话。`)) return
  busy.value = true; error.value = notice.value = ''
  try { await props.api.deleteEnvironment(props.bridge.id, env.id); Object.assign(dialog, { open: false, env: null }); notice.value = `已删除环境「${env.name}」。` } catch (e) { error.value = bridgeError(e) } finally { busy.value = false }
}
</script>

<template>
  <section class="lb-envs" :aria-label="`开发环境：${bridge.name}`">
    <div class="lb-envs-head">
      <div><h3>开发环境</h3><p>选择环境，浏览会话的工作目录并预览文件。智能体在环境指定的代码仓库里、按它的权限干活。</p></div>
      <button v-if="canWrite && DEV_PLATFORMS.length" type="button" class="primary-button lb-head-btn" @click="openDialog()"><Plus aria-hidden="true" />新增环境</button>
    </div>
    <p v-if="!DEV_PLATFORMS.length" role="note" class="lb-warn">这个 MCP 管理器没有控制带开发模式的平台（Codex / Claude / CodeBuddy），不能新增开发环境。</p>
    <ActionFeedback :error="error" :notice="notice" />
    <p v-if="environments.isPending.value" role="status" class="data-state">正在读取开发环境…</p>
    <div v-else-if="environments.error.value" class="data-state">
      <p role="alert">{{ bridgeError(environments.error.value) }}</p>
      <button type="button" class="secondary-button" @click="environments.refetch()">重试</button>
    </div>
    <p v-else-if="!list.length" class="ws-state-line">还没有开发环境。{{ canWrite ? '点“新增环境”添加一个。' : '' }}</p>
    <div v-else class="lb-envs-layout">
      <!-- 远程环境：容器信息条占满一行，下面照旧是三栏 -->
      <RemoteContainerBar
        v-if="selected?.location === 'remote' && selected.remote_container" :key="selected.remote_container" :api="api" :bridge-id="bridge.id"
        :container-id="selected.remote_container" :container="selectedContainer" :label="selected.remote_label ?? null" :can-write="canWrite"
      />
      <aside class="lb-envs-side">
        <nav aria-label="开发环境列表">
          <h4>开发环境<span class="lb-count-pill">{{ list.length }}</span></h4>
          <ul class="lb-env-list">
            <li v-for="e in list" :key="e.id">
              <button type="button" class="lb-env-item" :aria-current="e.id === selectedId ? 'true' : undefined" :title="`${SANDBOX[e.sandbox as keyof typeof SANDBOX] ?? e.sandbox} · ${e.network ? '可以联网' : '不联网'}${e.repo_url ? ` · ${e.repo_url}` : ''}`" @click="selectedId = e.id">
                <span class="lb-env-dot" :data-on="e.enabled" aria-hidden="true" />
                <span class="lb-env-text"><strong>{{ e.name }}</strong><span>{{ platformName(e.platform) }}<template v-if="!e.enabled"> · 已停用</template></span>
                  <span v-if="e.location === 'remote'" class="lb-env-place">{{ placeText(e) }}<span v-if="containerOf(e)" class="asv-state" :class="CONTAINER_STATE[containerOf(e)!.state].tone">{{ CONTAINER_STATE[containerOf(e)!.state].text }}</span></span>
                </span>
              </button>
            </li>
          </ul>
        </nav>
        <div v-if="selected && session && browsable" class="lb-envs-tree">
          <h4>文件夹</h4>
          <ul role="tree" aria-label="文件夹">
            <WorkTreeNode :key="session" :api="api" :bridge-id="bridge.id" :session="session" path="" :name="rootName(selected.repo_url)" :current="path" @open="path = $event" />
          </ul>
        </div>
      </aside>
      <BridgeWorkFiles
        v-if="selected" v-model:session="session" v-model:path="path" :api="api" :bridge-id="bridge.id" :env="selected"
        :sessions="sessionList" :sessions-loading="sessions.isPending.value" :can-write="canWrite" @settings="openDialog(selected!)"
      />
    </div>
    <EnvironmentDialog v-if="canWrite && dialog.open" :api="api" :bridge-id="bridge.id" :env="dialog.env" :platforms="DEV_PLATFORMS" @close="dialog.open = false" @saved="saved" @remove="remove" />
  </section>
</template>
