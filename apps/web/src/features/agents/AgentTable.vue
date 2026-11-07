<script setup lang="ts">
import { computed, ref } from 'vue'
import { STATUS } from './queries'
import { bytes, percent, rate } from './host'
import { AGENT_ALERT, type AgentRow } from './servers'

// 服务器详情里的智能体表格（T-0147）：每个智能体一行，可按列排序；色块与上面堆叠条的颜色一致
// （颜色跟着智能体，按名字的固定顺序分配，排序不改颜色）。点“查看趋势”在下面展开这个智能体自己的趋势。
const props = defineProps<{ rows: AgentRow[]; slots: Map<string, number>; selected: string | null }>()
const emit = defineEmits<{ select: [instanceId: string | null] }>()
type Key = 'name' | 'status' | 'cpu' | 'mem' | 'pids' | 'data' | 'tokens'
const COLUMNS: { key: Key; label: string; num?: boolean }[] = [
  { key: 'name', label: '智能体' }, { key: 'status', label: '状态' }, { key: 'cpu', label: 'CPU（占整机）', num: true },
  { key: 'mem', label: '内存', num: true }, { key: 'pids', label: '进程', num: true }, { key: 'data', label: '数据目录', num: true },
  { key: 'tokens', label: '1 小时 token', num: true },
]
const sortKey = ref<Key>('cpu'), descending = ref(true)
const STATUS_ORDER = { online: 0, offline: 1, pending: 2, disabled: 3 } as const
const value = (row: AgentRow, key: Key): number | string => {
  switch (key) {
    case 'name': return row.name
    case 'status': return STATUS_ORDER[row.status]
    case 'cpu': return row.load?.cpu_percent ?? -1
    case 'mem': return row.load?.mem_bytes ?? -1
    case 'pids': return row.load?.pids ?? -1
    case 'data': return row.load?.data_bytes ?? -1
    case 'tokens': return row.tokens_1h
  }
}
const sorted = computed(() => [...props.rows].sort((a, b) => {
  const x = value(a, sortKey.value), y = value(b, sortKey.value)
  const order = typeof x === 'string' && typeof y === 'string' ? x.localeCompare(y, 'zh-CN') : Number(x) - Number(y)
  return (descending.value ? -order : order) || a.name.localeCompare(b.name, 'zh-CN')
}))
function sortBy(key: Key) {
  if (sortKey.value === key) descending.value = !descending.value
  else { sortKey.value = key; descending.value = key !== 'name' && key !== 'status' }
}
const aria = (key: Key) => sortKey.value === key ? (descending.value ? 'descending' : 'ascending') : 'none'
const mem = (row: AgentRow) => {
  const l = row.load
  if (!l || !l.available || l.mem_bytes === null) return '无法获取'
  return `${bytes(l.mem_bytes)}${l.mem_limit ? ` / ${bytes(l.mem_limit)}` : ''}`
}
const cpu = (row: AgentRow) => {
  const l = row.load
  if (!l || !l.available) return '无法获取'
  return `${percent(l.cpu_percent)}${l.cpu_cores !== null ? `（${l.cpu_cores.toFixed(2)} 核）` : ''}`
}
</script>

<template>
  <div class="server-table-scroll" tabindex="0" aria-label="智能体表格滚动区">
    <table class="server-table">
      <caption class="sr-only">这台服务器上的智能体，点列名排序</caption>
      <thead>
        <tr>
          <th v-for="c in COLUMNS" :key="c.key" scope="col" :class="{ 'is-num': c.num }" :aria-sort="aria(c.key)">
            <button type="button" @click="sortBy(c.key)">{{ c.label }}<span aria-hidden="true">{{ sortKey === c.key ? (descending ? '↓' : '↑') : '' }}</span></button>
          </th>
          <th scope="col">网络（下 / 上）</th><th scope="col">正在执行</th><th scope="col">提醒</th><th scope="col"><span class="sr-only">操作</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in sorted" :key="row.instance_id" :class="{ 'is-selected': selected === row.instance_id }">
          <th scope="row">
            <span class="server-agent-name">
              <span class="share-swatch" :class="(slots.get(row.instance_id) ?? 99) <= 8 ? `is-${slots.get(row.instance_id)}` : 'is-other'" aria-hidden="true" />{{ row.name }}
            </span>
            <span v-if="!row.deployed" class="form-help">（手动接入）</span>
          </th>
          <td>{{ STATUS[row.status] }}<template v-if="row.load?.stale"> · 数据过期</template></td>
          <td class="is-num">{{ cpu(row) }}</td>
          <td class="is-num" :class="{ 'is-danger': row.alerts.includes('mem_near_limit') }">{{ mem(row) }}</td>
          <td class="is-num">{{ row.load?.pids ?? '—' }}</td>
          <td class="is-num">{{ row.load?.data_bytes === null || row.load?.data_bytes === undefined ? '—' : `${row.load.data_truncated ? '≥ ' : ''}${bytes(row.load.data_bytes)}` }}</td>
          <td class="is-num">{{ row.tokens_1h.toLocaleString('zh-CN') }}</td>
          <td>{{ rate(row.load?.rx_rate) }} / {{ rate(row.load?.tx_rate) }}</td>
          <td>{{ row.running_task?.title ?? '—' }}</td>
          <td :class="{ 'is-danger': row.alerts.length }">{{ row.alerts.length ? row.alerts.map(a => AGENT_ALERT[a]).join('、') : '—' }}<template v-if="row.load?.restarts_1h"> · 1 小时重启 {{ row.load.restarts_1h }} 次</template></td>
          <td><button type="button" class="secondary-button" :aria-pressed="selected === row.instance_id" @click="emit('select', selected === row.instance_id ? null : row.instance_id)">{{ selected === row.instance_id ? '收起趋势' : '查看趋势' }}</button></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
