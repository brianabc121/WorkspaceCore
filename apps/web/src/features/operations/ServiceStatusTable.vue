<script setup lang="ts">
import type { ServiceItem } from './display'
import { serviceKinds, serviceReason } from './display'
import { formatTime } from '../time'
import ServiceStateBadge from './ServiceStateBadge.vue'

// 服务状态表（T-0177）：调用方已按“异常在前、名称稳定”排好序；点名称（或整行）在右侧看详情。
// 说明只显示工作台自己的文案（serviceReason），不回显接口的原始 reason。
defineProps<{ items: readonly ServiceItem[]; selected: string | null }>()
const emit = defineEmits<{ select: [id: string] }>()
</script>

<template>
  <div class="ws-table-wrap ops-table-wrap">
    <table class="ws-table ops-table" aria-label="服务状态列表">
      <thead>
        <tr><th scope="col">服务</th><th scope="col" class="ops-col-kind">类型</th><th scope="col">状态</th><th scope="col" class="ops-col-time">最近检查</th><th scope="col" class="ops-col-reason">说明</th></tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.id" :aria-label="item.name" :aria-selected="selected === item.id" :data-state="item.status" @click="emit('select', item.id)">
          <td>
            <button type="button" class="ops-service-button" :aria-pressed="selected === item.id" @click.stop="emit('select', item.id)">{{ item.name }}</button>
            <span v-if="serviceReason(item)" class="ops-inline-reason">{{ serviceReason(item) }}</span>
          </td>
          <td class="ops-col-kind">{{ serviceKinds[item.kind] }}</td>
          <td><ServiceStateBadge :status="item.status" /></td>
          <td class="ops-col-time"><time v-if="item.checked_at" :datetime="item.checked_at">{{ formatTime(item.checked_at) }}</time><span v-else class="ops-muted">暂无记录</span></td>
          <td class="ops-col-reason">{{ serviceReason(item) || '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
