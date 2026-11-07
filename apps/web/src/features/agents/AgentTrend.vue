<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { agentError } from './queries'
import { useHostStatus, type HostRange } from './host'
import HostChart from './HostChart.vue'

// 单个智能体自己的趋势（T-0147）：它的 OpenClaw 容器每小时的 CPU（占整机）、内存、网速，与整机趋势分开看。
const props = defineProps<{ instanceId: string; name: string; range: HostRange }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const status = useHostStatus(scope, () => props.instanceId, () => props.range, () => true)
const data = computed(() => status.data.value?.instance_id === props.instanceId ? status.data.value : undefined)
</script>

<template>
  <section class="host-detail" :aria-label="`${name} 的趋势`">
    <h4>{{ name }} 的趋势</h4>
    <p v-if="status.isPending.value" role="status">正在加载…</p>
    <p v-else-if="status.error.value" role="alert">{{ agentError(status.error.value) }}</p>
    <div v-else-if="data" class="host-charts">
      <HostChart title="CPU（占整机）" kind="percent" :range="range" :points="data.agent_points" :series="[{ key: 'cpu_avg', label: '平均' }, { key: 'cpu_max', label: '峰值' }]" />
      <HostChart title="内存已用" :note="data.agent?.mem_limit ? `上限 ${Math.round(data.agent.mem_limit / 1048576)} MiB` : undefined" kind="bytes" :range="range" :points="data.agent_points" :series="[{ key: 'mem_avg', label: '平均' }, { key: 'mem_max', label: '峰值' }]" />
      <HostChart title="网络" note="每小时平均，悬停看峰值" kind="rate" :range="range" :points="data.agent_points" :series="[{ key: 'rx_avg', label: '下载' }, { key: 'tx_avg', label: '上传' }]" :extra="[{ key: 'rx_max', label: '下载峰值' }, { key: 'tx_max', label: '上传峰值' }]" />
    </div>
  </section>
</template>
