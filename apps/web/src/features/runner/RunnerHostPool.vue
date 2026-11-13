<script setup lang="ts">
import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { bytes, HOST_STATUS, runnerError } from './api'
import ContainerHostStrip from './ContainerHostStrip.vue'

const props = defineProps<{ hosts: Schema['RunnerHostOut'][]; selectedId?: string }>()
const emit = defineEmits<{ select: [id: string]; settings: [] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const candidates = useQuery({
  queryKey: ['runner', user, scope, 'candidates'],
  enabled: computed(() => auth.user?.id === user && auth.workspaceId === scope && auth.canManage),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/runner/hosts/candidates', { params: { header: { 'X-Workspace-Id': scope } }, signal })),
  refetchInterval: 30_000,
})
const selected = computed(() => props.hosts.find(h => h.id === props.selectedId))
const candidate = computed(() => candidates.data.value?.items.find(h => h.id === props.selectedId))
const reasons: Record<string, string> = { offline: '节点离线', disabled: '节点已停用', metrics_stale: '资源采样已过期', metrics_unknown: '资源状态未知', runtime_unavailable: '运行时未就绪', network_unavailable: '网络未就绪', memory_insufficient: '内存不足', cpu_insufficient: 'CPU 不足', disk_insufficient: '磁盘不足' }
</script>

<template>
  <section class="ws-panel ws-panel-body" aria-label="容器服务器池">
    <h2>容器服务器池</h2>
    <div class="form-actions">
      <button v-for="host in hosts" :key="host.id" type="button" class="secondary-button" :aria-pressed="host.id === selectedId" @click="emit('select', host.id)">{{ host.name }} · {{ HOST_STATUS[host.status] }}</button>
    </div>
    <p v-if="candidates.error.value" role="alert">调度状态刷新失败：{{ runnerError(candidates.error.value) }}</p>
    <template v-if="selected">
      <ContainerHostStrip :host="selected" @settings="emit('settings')" />
      <dl class="agent-facts">
        <div><dt>运行后端 / 节点</dt><dd>{{ selected.backend ?? 'docker' }} · {{ selected.node_name ?? '—' }}</dd></div>
        <div><dt>运行时</dt><dd>{{ selected.runtimes.join('、') || '暂无上报' }}</dd></div>
        <template v-if="candidate">
          <div><dt>自动调度</dt><dd>{{ candidate.eligible ? '可参与分配' : '暂不可分配' }}</dd></div>
          <div><dt>排除原因</dt><dd>{{ candidate.reasons.map(r => reasons[r] ?? r).join('；') || '无' }}</dd></div>
          <div><dt>CPU 负载 / 容量</dt><dd>{{ candidate.cpu_load ?? '—' }} / {{ candidate.cpus ?? '—' }} 核</dd></div>
          <div><dt>可用内存</dt><dd>{{ candidate.available_memory_mb === null ? '—' : bytes(candidate.available_memory_mb * 1024 ** 2) }}</dd></div>
          <div><dt>可用磁盘</dt><dd>{{ candidate.disk_free_gb ?? '—' }} GB</dd></div>
          <div><dt>有效承诺 / 创建预约</dt><dd>{{ candidate.committed_cpus }} 核 · {{ candidate.committed_memory_mb }} MiB · {{ candidate.committed_disk_gb }} GB</dd></div>
          <div><dt>资源采样时间</dt><dd>{{ candidate.observed_at ? formatTime(candidate.observed_at) : '未知（不能按空闲处理）' }}</dd></div>
        </template>
        <div v-else><dt>调度资源</dt><dd>{{ candidates.isPending.value ? '正在加载…' : '暂无有效采样' }}</dd></div>
      </dl>
    </template>
  </section>
</template>
