<script setup lang="ts">
import { computed, ref } from 'vue'
import { Bot, ChevronRight, LoaderCircle, X } from '@lucide/vue'
import { ApiError } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { formatTime } from '../time'
import { RUN_STATUS, runnerError, seconds, STATE, useRunnerEnvironment, useRunnerRuns, type RunFilter } from './api'
import { shortId, STATE_TONE } from './containerMetrics'
import ContainerResourceMeters from './ContainerResourceMeters.vue'
import ContainerLifecyclePanel from './ContainerLifecyclePanel.vue'
import ContainerNetworkPanel from './ContainerNetworkPanel.vue'

// 容器详情（T-0187，效果图右侧一栏；手机放进右侧抽屉）：按完整 UUID 读 GET /runner/environments/{id}，
// 所以不在当前筛选、不在当前页的容器也能直接打开（地址里的 environment_id、通知里的链接）。
// 不存在或已无权访问时不留旧内容。资源负载、执行记录、配置与回收、网络与服务分开。
// 打开、刷新详情只读，不会启动、停止、归档或删除任何东西。
type Section = 'load' | 'runs' | 'config' | 'network'
const props = defineProps<{ environmentId: string; aside?: boolean }>()
const emit = defineEmits<{ close: []; runs: [environmentId: string] }>()
const scope = useAuthStore().workspaceId!
const id = computed(() => props.environmentId)
const detail = useRunnerEnvironment(scope, id)
const env = computed(() => detail.data.value ?? null)
const gone = computed(() => detail.error.value instanceof ApiError && [403, 404].includes(detail.error.value.status))
const section = ref<Section>('load')
const SECTIONS: Record<Section, string> = { load: '资源负载', runs: '执行记录', config: '配置与回收', network: '网络与服务' }

const runFilter = computed<RunFilter>(() => ({ environment_id: props.environmentId }))
const runs = useRunnerRuns(scope, runFilter)
const runItems = computed(() => runs.data.value?.items ?? [])
const active = computed(() => runItems.value.filter(r => r.status === 'running' || r.status === 'queued'))
</script>

<template>
  <div class="cs-detail" :class="{ 'is-aside': aside }">
    <div class="cs-detail-head">
      <div class="cs-detail-title">
        <h2 v-if="env"><span class="preserve-text">{{ env.untrusted.purpose }}</span> <span class="asv-state" :class="`tone-${STATE_TONE[env.state] ?? 'muted'}`">{{ STATE[env.state] ?? '未知状态' }}</span></h2>
        <h2 v-else>容器详情</h2>
        <p class="asv-sub"><span :title="environmentId">{{ shortId(environmentId) }}</span><template v-if="env"> · <span class="cs-owner" :class="{ 'is-gone': !env.owner_instance_id }"><Bot aria-hidden="true" />{{ env.owner_instance_id ? env.owner : '原智能体已删除' }}</span></template></p>
      </div>
      <button v-if="aside" type="button" class="icon-button" aria-label="关闭容器详情" @click="emit('close')"><X aria-hidden="true" /></button>
    </div>

    <p v-if="detail.isPending.value" role="status" class="ws-state-line">正在加载容器…</p>
    <div v-else-if="gone" class="ws-state-line"><p role="alert">这个容器不存在，或你已无权访问。</p><button type="button" class="secondary-button" @click="emit('close')">关闭</button></div>
    <div v-else-if="detail.error.value && !env" class="ws-state-line"><p role="alert">{{ runnerError(detail.error.value) }}</p><button type="button" class="secondary-button" @click="detail.refetch()">重试</button></div>
    <template v-else-if="env">
      <div class="cs-tabs" role="tablist" aria-label="容器详情">
        <button v-for="(name, key) in SECTIONS" :key="key" type="button" role="tab" :aria-selected="section === key" @click="section = key">{{ name }}<template v-if="key === 'runs' && active.length"> · {{ active.length }}</template></button>
      </div>
      <div role="tabpanel" :aria-label="SECTIONS[section]">
        <template v-if="section === 'load'">
          <ContainerResourceMeters :env="env" />
          <section class="cs-block" aria-label="当前执行">
            <h3>当前执行</h3>
            <p v-if="runs.isPending.value" role="status" class="form-help">正在加载…</p>
            <p v-else-if="!active.length" class="form-help">现在没有排队或执行中的命令。</p>
            <ul v-else class="cs-runs">
              <li v-for="r in active" :key="r.run_id"><LoaderCircle aria-hidden="true" class="cs-spin" /><code class="runner-command">{{ r.command }}</code><span class="badge">{{ RUN_STATUS[r.status] }}</span></li>
            </ul>
            <button type="button" class="asv-link" @click="section = 'runs'">查看执行记录<ChevronRight aria-hidden="true" /></button>
          </section>
          <p v-if="env.state_reason" class="form-help">原因：<span class="cs-break">{{ env.state_reason }}</span></p>
        </template>
        <section v-else-if="section === 'runs'" class="cs-block" aria-label="这个容器的执行记录">
          <p v-if="runs.isPending.value" role="status" class="form-help">正在加载执行记录…</p>
          <p v-else-if="runs.error.value" role="alert">加载失败，请稍后重试。</p>
          <p v-else-if="!runItems.length" class="form-help">这个容器还没有执行记录。</p>
          <template v-else>
            <p class="form-help">当前已加载 {{ runItems.length }} 条（最近的在前，最多 100 条）。排队、超时、结果未知是命令的状态，不代表容器停止。</p>
            <ul class="cs-runs">
              <li v-for="r in runItems" :key="r.run_id">
                <code class="runner-command">{{ r.command }}</code>
                <span class="badge">{{ RUN_STATUS[r.status] }}</span><span v-if="r.exit_code !== null" class="badge">退出码 {{ r.exit_code }}</span>
                <span class="asv-sub">{{ r.instance ?? '已删除的实例' }} · {{ r.started_at ? formatTime(r.started_at) : '还没开始' }} · 耗时 {{ seconds(r.seconds) }}</span>
              </li>
            </ul>
          </template>
          <button type="button" class="asv-link" @click="emit('runs', environmentId)">在执行记录页查看输出<ChevronRight aria-hidden="true" /></button>
        </section>
        <ContainerNetworkPanel v-else-if="section === 'network'" :environment-id="environmentId" />
        <ContainerLifecyclePanel v-else :env="env" @refresh="detail.refetch()" />
      </div>
      <p v-if="detail.error.value" class="form-help">刷新失败，显示的是上次取回的内容。</p>
    </template>
  </div>
</template>
