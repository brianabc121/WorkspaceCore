<script setup lang="ts">
import { computed } from 'vue'
import { Activity, ArrowRight, Box, Link as LinkIcon, Server as ServerIcon, Bot, X } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { formatTime } from '../time'
import { ago, deployTask, imStatus, type OverviewRow } from './display'
import { useDeployment } from './servers'

// 智能体详情的概览（T-0182，效果图 02 下半部分）：所在服务器、部署方式、部署任务、消息连接与最近心跳分别显示。
// 没有真实的容器检测时容器状态写“未检测”，不拿消息在线 / 离线冒充容器运行 / 停止；实时状态在下面的运维里“查看状态”。
// 下面的 InstanceCard 保留原有的全部能力（配置、权限、接入凭证、运维、负载、唤醒、发件箱、停用 / 启用、资源上限等）。
const props = defineProps<{ instance: Schema['AgentInstanceOut']; row: OverviewRow | null }>()
const emit = defineEmits<{ close: [] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const state = useDeployment(scope, () => props.instance.id)
const deployment = computed(() => state.data.value?.deployment ?? props.row?.deployment ?? null)
const server = computed(() => state.data.value?.server ?? props.row?.server ?? null)
const job = computed(() => state.data.value?.job ?? props.row?.latest_job ?? null)
const task = computed(() => deployTask(job.value, deployment.value))
const im = computed(() => imStatus(props.instance.status))
const method = computed(() => deployment.value ? '平台自动部署（Docker Compose）' : props.instance.server_manual ? '手动接入 · 手动指定服务器' : server.value ? '手动接入 · 按上报识别服务器' : props.instance.network_mode === 'intranet' ? '手动接入（内网）' : '手动接入')
</script>

<template>
  <section class="ag-detail-head" aria-label="智能体概览">
    <div class="ag-detail-title">
      <span class="ag-avatar is-large" aria-hidden="true"><Bot /></span>
      <div>
        <h2>{{ instance.name }}</h2>
        <p class="asv-sub">{{ row?.deployment_project ?? instance.im_nickname }}</p>
      </div>
      <RouterLink v-if="server" class="asv-go" :to="{ path: '/agent-servers', query: { server: server.id } }">查看服务器<ArrowRight aria-hidden="true" /></RouterLink>
      <button type="button" class="icon-button asv-close" aria-label="关闭智能体详情" @click="emit('close')"><X aria-hidden="true" /></button>
    </div>
    <dl class="ag-facts">
      <div><dt><ServerIcon aria-hidden="true" />所在服务器</dt><dd><RouterLink v-if="server" :to="{ path: '/agent-servers', query: { server: server.id } }">{{ server.name }}</RouterLink><template v-else>未归属</template></dd></div>
      <div><dt><Box aria-hidden="true" />部署方式</dt><dd>{{ method }}</dd></div>
      <div><dt><Activity aria-hidden="true" />部署任务</dt><dd><span class="asv-state" :class="`tone-${task.tone}`">{{ task.label }}</span><span v-if="job" class="asv-sub">{{ formatTime(job.finished_at ?? job.started_at) }}</span></dd></div>
      <div><dt><LinkIcon aria-hidden="true" />消息连接</dt><dd><span class="asv-state" :class="`tone-${im.tone}`">{{ im.label }}</span><span class="asv-sub">最近心跳 {{ ago(instance.last_seen_at) }}</span></dd></div>
      <div><dt><Box aria-hidden="true" />容器状态</dt><dd>未检测<span class="asv-sub">{{ deployment ? '在下方“运维”里点“查看状态”实时检查' : '手动接入的实例，工作台不能检查它的容器' }}</span></dd></div>
    </dl>
  </section>
</template>
