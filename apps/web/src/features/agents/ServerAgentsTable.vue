<script setup lang="ts">
import { Bot, ArrowRight } from '@lucide/vue'
import { formatTime } from '../time'
import { IM_STATUS } from './queries'
import { DEPLOYMENT_STATE, limitText, type AgentRow } from './servers'
import { SSH_JOB_KIND, SSH_JOB_STATUS } from './ssh'

// 服务器详情里的“已部署 OpenClaw”（T-0181，改造规范第 3.1、6.3 节）：这台服务器上的每个智能体——
// 部署状态（以部署记录与最近任务为准）、消息连接、平台确定的 Compose 项目标识、资源上限、最新任务，跳到智能体详情。
// 没有真实的容器检测结果，所以这里不写“运行中 / 已停止”，也不猜容器名。
defineProps<{ rows: AgentRow[] }>()
</script>

<template>
  <div class="ws-table-wrap">
    <table class="ws-table asv-table asv-agents">
      <thead><tr><th scope="col">智能体</th><th scope="col">部署状态</th><th scope="col">消息连接</th><th scope="col">部署项目</th><th scope="col">资源上限</th><th scope="col">最新任务</th><th scope="col"><span class="sr-only">操作</span></th></tr></thead>
      <tbody>
        <tr v-for="row in rows" :key="row.instance_id">
          <th scope="row" data-label="智能体"><span class="asv-name"><Bot aria-hidden="true" />{{ row.name }}</span></th>
          <td data-label="部署状态"><span class="asv-state" :class="`tone-${DEPLOYMENT_STATE[row.deployment_state].tone}`">{{ DEPLOYMENT_STATE[row.deployment_state].label }}</span></td>
          <td data-label="消息连接">{{ IM_STATUS[row.status] }}</td>
          <td data-label="部署项目"><code v-if="row.deployment_project" class="asv-code">{{ row.deployment_project }}</code><template v-else>—</template></td>
          <td data-label="资源上限">{{ limitText(row.cpus, row.mem_limit_mb) }}</td>
          <td data-label="最新任务">
            <template v-if="row.latest_job">{{ SSH_JOB_KIND[row.latest_job.kind] }} · {{ SSH_JOB_STATUS[row.latest_job.status] }}<span class="asv-sub">{{ formatTime(row.latest_job.finished_at ?? row.latest_job.started_at) }}</span></template>
            <template v-else>—</template>
          </td>
          <td class="asv-link-cell"><RouterLink :to="{ path: '/agents', query: { instance: row.instance_id } }" :aria-label="`查看智能体：${row.name}`">查看智能体<ArrowRight aria-hidden="true" /></RouterLink></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
