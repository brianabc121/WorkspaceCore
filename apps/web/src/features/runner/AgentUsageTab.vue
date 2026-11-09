<script setup lang="ts">
import { Bot } from '@lucide/vue'
import type { Schema } from '../../services/api'

// 智能体用量（T-0186）：每个打开过“运行环境权限”或还有在用环境的实例——权限、运行中 / 上限、常驻 / 上限、
// 已分配的内存与 CPU / 上限。这里的内存与 CPU 是运行中环境的“配置额度之和”，不是实测用量（实测在容器总览里看）。
// 点“查看容器”回到容器总览并只看这个智能体申请的容器。权限开关仍在 OpenClaw 智能体页的实例详情里。
defineProps<{ instances: Schema['RunnerInstanceUsage'][] }>()
defineEmits<{ containers: [instanceId: string] }>()
const limit = (item: Schema['RunnerInstanceUsage'], key: string) => item.limits[key] ?? 0
</script>

<template>
  <section class="ws-panel asv-list" aria-label="智能体用量">
    <p class="form-help cs-help">内存、CPU 是运行中环境的<strong>已分配额度 / 上限</strong>，不是实测用量；实测读数在“容器总览”。上限在 OpenClaw 智能体页的实例详情 → 运行环境权限里改。</p>
    <p v-if="!instances.length" class="ws-state-line">还没有实例打开运行环境权限（在 OpenClaw 智能体页的实例详情里打开）。</p>
    <div v-else class="ws-table-wrap">
      <table class="ws-table asv-table cs-usage">
        <thead><tr><th scope="col">智能体</th><th scope="col">运行环境权限</th><th scope="col">运行中 / 上限</th><th scope="col">常驻 / 上限</th><th scope="col">已分配内存（MiB）/ 上限</th><th scope="col">已分配 CPU（核）/ 上限</th><th scope="col"><span class="sr-only">操作</span></th></tr></thead>
        <tbody>
          <tr v-for="item in instances" :key="item.instance_id">
            <th scope="row" data-label="智能体"><span class="asv-name"><Bot aria-hidden="true" /><span>{{ item.name }}</span></span></th>
            <td data-label="运行环境权限"><span class="asv-state" :class="item.enabled ? 'tone-green' : 'tone-muted'">{{ item.enabled ? '已打开' : '已关闭' }}</span></td>
            <td data-label="运行中 / 上限">{{ item.running }} / {{ limit(item, 'envs') }}</td>
            <td data-label="常驻 / 上限">{{ item.persistent }} / {{ limit(item, 'persistent') }}</td>
            <td data-label="已分配内存（MiB）/ 上限">{{ item.memory_mb }} / {{ limit(item, 'memory_mb') }}</td>
            <td data-label="已分配 CPU（核）/ 上限">{{ item.cpus }} / {{ limit(item, 'cpus') }}</td>
            <td class="asv-link-cell"><button type="button" class="asv-link" :aria-label="`查看容器：${item.name}`" @click="$emit('containers', item.instance_id)">查看容器</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
