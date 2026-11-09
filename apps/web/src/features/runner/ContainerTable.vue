<script setup lang="ts">
import { Bot, Box } from '@lucide/vue'
import { LIFETIME, STATE, type RunnerEnv } from './api'
import { ago, cpuReading, memoryReading, shortId, STATE_TONE, volumeReading, type Reading } from './containerMetrics'

// 容器表（T-0186，效果图中间的表格）：用途 + 短 ID、申请智能体、类别、状态、CPU 已用 / 上限、内存已用 / 上限、
// 数据卷占用、详情。行用完整 UUID 标识（同名用途不会串）；用途是智能体写的，只当文字显示。
// 读数的口径与“未知 / 过期”在 containerMetrics.ts；这里只负责摆放。点行或“详情”发出 select。
defineProps<{ items: RunnerEnv[]; selectedId: string | null }>()
const emit = defineEmits<{ select: [id: string, row: HTMLElement | null] }>()
function title(r: Reading, label: string) {
  if (r.status === 'not_running') return `${label}：环境没在运行`
  if (r.status === 'legacy') return `${label}：旧版读数，上报时间未知`
  if (r.status === 'unavailable') return `${label}：${r.note || '未知'}`
  return `${label}：${r.status === 'stale' ? '已过期，' : ''}上报于 ${ago(r.at)}`
}
function pick(env: RunnerEnv, event: Event) {
  const row = (event.currentTarget as HTMLElement | null)?.closest('tr') ?? null
  emit('select', env.id, row)
}
</script>

<template>
  <div class="ws-table-wrap">
    <table class="ws-table asv-table cs-table">
      <thead>
        <tr>
          <th scope="col">容器</th><th scope="col">申请智能体</th><th scope="col">类别</th><th scope="col">状态</th>
          <th scope="col">CPU 已用 / 上限</th><th scope="col">内存 已用 / 上限</th><th scope="col">数据卷占用</th><th scope="col"><span class="sr-only">操作</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="env in items" :key="env.id" :aria-selected="selectedId === env.id" tabindex="0" :data-env="env.id" @click="pick(env, $event)" @keydown.enter.self="pick(env, $event)">
          <th scope="row" data-label="容器">
            <span class="asv-name"><Box aria-hidden="true" /><span class="preserve-text cs-purpose">{{ env.untrusted.purpose }}</span></span>
            <span class="asv-sub cs-id" :title="env.id">{{ shortId(env.id) }}</span>
          </th>
          <td data-label="申请智能体"><span class="cs-owner" :class="{ 'is-gone': !env.owner_instance_id }"><Bot aria-hidden="true" />{{ env.owner_instance_id ? env.owner : '原智能体已删除' }}</span></td>
          <td data-label="类别"><span class="cs-chip" :class="`is-${env.lifetime}`">{{ LIFETIME[env.lifetime] }}</span><span v-if="env.pinned" class="cs-chip is-pinned">已保护</span></td>
          <td data-label="状态"><span class="asv-state" :class="`tone-${STATE_TONE[env.state] ?? 'muted'}`">{{ STATE[env.state] ?? '未知状态' }}</span></td>
          <td v-for="(r, i) in [cpuReading(env), memoryReading(env)]" :key="i" :data-label="i === 0 ? 'CPU 已用 / 上限' : '内存 已用 / 上限'" class="cs-meter-cell" :title="title(r, i === 0 ? 'CPU' : '内存')">
            <span class="cs-reading" :class="`is-${r.status}`"><b>{{ r.value }}</b> <span v-if="r.limit" class="cs-limit">{{ r.limit }}</span></span>
            <span v-if="r.percent !== null" class="cs-bar-line">
              <span class="cs-bar" :class="[i === 0 ? 'is-cpu' : 'is-mem', `is-${r.status}`]" role="meter" :aria-label="`${i === 0 ? 'CPU' : '内存'}占上限`" :aria-valuenow="Math.round(r.percent)" aria-valuemin="0" aria-valuemax="100"><span :style="{ width: `${r.bar}%` }" /></span>
              <span class="cs-pct">{{ Math.round(r.percent) }}%</span>
            </span>
            <span v-if="r.note && r.status !== 'not_running'" class="cs-note" :class="`is-${r.status}`">{{ r.note }}</span>
          </td>
          <td data-label="数据卷占用" :title="title(volumeReading(env), '数据卷')">
            <span class="cs-reading" :class="`is-${volumeReading(env).status}`"><b>{{ volumeReading(env).value }}</b></span>
            <span v-if="volumeReading(env).note" class="cs-note" :class="`is-${volumeReading(env).status}`">{{ volumeReading(env).note }}</span>
          </td>
          <td class="asv-link-cell">
            <button type="button" class="asv-link" :aria-label="`${env.state === 'failed' ? '查看原因' : '详情'}：${env.untrusted.purpose}（${shortId(env.id)}）`" @click.stop="pick(env, $event)">{{ env.state === 'failed' ? '查看原因' : '详情' }}</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
