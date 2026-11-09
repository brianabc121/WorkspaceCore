<script setup lang="ts">
import { KeyRound } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { SECRET_KIND } from './queries'
import { grantedInstances } from './secrets'

// 密钥表（T-0196，效果图 07）：名称、类型、当前值版本、授权对象、最近轮换与“管理”。只有元数据，没有值、复制或有效期。
// 点一行或“管理”打开右侧详情；授权对象超过三个时只列前三个。
defineProps<{ items: Schema['AgentSecretOut'][]; selected: string | null }>()
const emit = defineEmits<{ select: [id: string, row: HTMLElement | null] }>()
function granted(secret: Schema['AgentSecretOut']) {
  const names = grantedInstances(secret).map(i => i.name)
  if (!names.length) return '未授权'
  return names.length > 3 ? `${names.slice(0, 3).join('、')} 等 ${names.length} 个` : names.join('、')
}
const row = (event: Event) => (event.currentTarget as HTMLElement | null)?.closest('tr') ?? null
</script>

<template>
  <div class="ws-table-wrap">
    <table class="ws-table asv-table sk-table">
      <thead><tr><th scope="col">名称</th><th scope="col">类型</th><th scope="col">当前版本</th><th scope="col">授权对象</th><th scope="col">最近轮换</th><th scope="col"><span class="sr-only">操作</span></th></tr></thead>
      <tbody>
        <tr v-for="s in items" :key="s.id" :data-secret="s.id" :aria-selected="selected === s.id" @click="emit('select', s.id, row($event))">
          <th scope="row" data-label="名称"><span class="asv-name"><KeyRound aria-hidden="true" /><span>{{ s.name }}</span></span></th>
          <td data-label="类型" class="asv-nowrap">{{ SECRET_KIND[s.kind] ?? s.kind }}</td>
          <td data-label="当前版本" class="asv-nowrap">v{{ s.value_version }}</td>
          <td data-label="授权对象" :class="{ 'sk-none': !s.grants.length }">{{ granted(s) }}</td>
          <td data-label="最近轮换" class="asv-nowrap"><time :datetime="s.last_rotated_at">{{ formatTime(s.last_rotated_at) }}</time></td>
          <td class="asv-link-cell"><button type="button" class="asv-link" :aria-label="`管理：${s.name}`" @click.stop="emit('select', s.id, row($event))">管理</button></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
