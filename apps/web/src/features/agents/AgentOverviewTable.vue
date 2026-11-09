<script setup lang="ts">
import { Bot } from '@lucide/vue'
import { formatTime } from '../time'
import { ago, deployTask, imStatus, type OverviewRow } from './display'
import { limitText } from './servers'

// OpenClaw 智能体表（T-0182，效果图 02）：智能体、所属服务器（链到服务器页）、连接状态、部署任务、资源上限、最近心跳、查看。
// 连接状态是消息连接（待接入 / 消息在线 / 离线 / 已停用），不是容器状态；部署任务另列。
defineProps<{ rows: OverviewRow[]; selected: string | null }>()
const emit = defineEmits<{ select: [instanceId: string] }>()
</script>

<template>
  <div class="ws-table-wrap">
    <table class="ws-table asv-table ag-table">
      <thead><tr><th scope="col">智能体</th><th scope="col">所属服务器</th><th scope="col">连接状态</th><th scope="col">部署任务</th><th scope="col">资源上限</th><th scope="col">最近心跳</th><th scope="col"><span class="sr-only">操作</span></th></tr></thead>
      <tbody>
        <tr v-for="row in rows" :key="row.instance.id" :aria-selected="selected === row.instance.id" @click="emit('select', row.instance.id)">
          <th scope="row" data-label="智能体">
            <span class="asv-name ag-name"><span class="ag-avatar" aria-hidden="true"><Bot /></span><span>{{ row.instance.name }}<span class="asv-sub">{{ row.deployment_project ?? row.instance.im_nickname }}</span></span></span>
          </th>
          <td data-label="所属服务器">
            <RouterLink v-if="row.server" :to="{ path: '/agent-servers', query: { server: row.server.id } }" @click.stop>{{ row.server.name }}</RouterLink>
            <span v-else class="asv-sub">未归属</span>
          </td>
          <td data-label="连接状态"><span class="asv-state" :class="`tone-${imStatus(row.instance.status).tone}`">{{ imStatus(row.instance.status).label }}</span></td>
          <td data-label="部署任务"><span class="asv-state" :class="`tone-${deployTask(row.latest_job, row.deployment).tone}`">{{ deployTask(row.latest_job, row.deployment).label }}</span></td>
          <td data-label="资源上限" class="asv-nowrap">{{ limitText(row.instance.limits?.cpus, row.instance.limits?.mem_limit_mb) }}</td>
          <td data-label="最近心跳" class="asv-nowrap"><time v-if="row.instance.last_seen_at" :datetime="row.instance.last_seen_at" :title="formatTime(row.instance.last_seen_at)">{{ ago(row.instance.last_seen_at) }}</time><template v-else>—</template></td>
          <td class="asv-link-cell"><button type="button" class="asv-link" :aria-label="`查看：${row.instance.name}`" @click.stop="emit('select', row.instance.id)">查看</button></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
