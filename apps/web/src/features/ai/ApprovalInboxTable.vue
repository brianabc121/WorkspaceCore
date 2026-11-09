<script setup lang="ts">
import { Bot, Clock } from '@lucide/vue'
import { RISK_LABELS, STATUS_LABELS, type Execution } from './queries'
import { RISK_TONE, SOURCE_ICONS, STATUS_TONE, clockTime, dayTime, formatRemaining, remainingSeconds, sourceOf, toolIcon } from './approvalFormat'

// 待处理表（T-0191，效果图 01 中间）：操作申请（平台生成的说明 + 工具名）、发起智能体、类型、剩余时间（待审批）或状态、
// 来源、查看。说明与名称都是外部文字，只当文本显示。剩余时间按服务端时钟（serverNow）算；还没校时就写“—”。
const props = defineProps<{ items: Execution[]; view: 'pending' | 'activity' | 'exceptions'; selectedId: string | null; serverNow: number | null; expiringWindow: number }>()
const emit = defineEmits<{ select: [id: string, row: HTMLElement | null] }>()

function countdown(e: Execution) {
  if (e.status !== 'pending_approval') return null
  const left = props.serverNow === null ? null : remainingSeconds(e.expires_at, props.serverNow)
  if (left === null) return { text: '—', tone: 'is-unknown', label: '剩余时间未知（还没有服务端时间）' }
  if (left === 0) return { text: '已到期', tone: 'is-expired', label: '已到期，不能再批准' }
  return { text: formatRemaining(left), tone: left <= props.expiringWindow ? 'is-soon' : '', label: `剩余 ${formatRemaining(left)}` }
}
function pick(e: Execution, event: Event) {
  emit('select', e.id, (event.currentTarget as HTMLElement | null)?.closest('tr') ?? null)
}
</script>

<template>
  <div class="ws-table-wrap ac-table-wrap">
    <table class="ws-table asv-table ac-table">
      <thead>
        <tr>
          <th scope="col">操作申请</th><th scope="col">发起智能体</th><th scope="col">类型</th>
          <th scope="col">{{ view === 'pending' ? '剩余时间' : '状态' }}</th><th scope="col">来源</th><th scope="col">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="e in items" :key="e.id" :aria-label="`申请：${e.summary}`" :aria-selected="selectedId === e.id" tabindex="0" :data-execution="e.id" @click="pick(e, $event)" @keydown.enter.self="pick(e, $event)">
          <th scope="row" data-label="操作申请">
            <span class="asv-name ac-name"><component :is="toolIcon(e.tool_name)" aria-hidden="true" /><span class="preserve-text ac-summary-text">{{ e.summary }}</span></span>
            <span class="asv-sub ac-tool"><code>{{ e.tool_name }}</code> · <template v-if="e.status === 'pending_approval' && e.expires_at">截止 <time :datetime="e.expires_at">{{ clockTime(e.expires_at) }}</time></template><time v-else :datetime="e.created_at">{{ dayTime(e.created_at) }}</time></span>
          </th>
          <td data-label="发起智能体" class="ac-col-agent">
            <span class="ac-agent" :class="{ 'is-gone': !e.instance_id }"><Bot aria-hidden="true" /><span class="preserve-text">{{ e.instance_name }}</span></span>
            <span v-if="!e.instance_id" class="asv-sub">（已删除）</span>
          </td>
          <td data-label="类型"><span class="ac-risk" :class="`tone-${RISK_TONE[e.risk_level]}`">{{ RISK_LABELS[e.risk_level] }}</span></td>
          <td :data-label="view === 'pending' ? '剩余时间' : '状态'" class="asv-nowrap">
            <span v-if="countdown(e)" class="ac-countdown" :class="countdown(e)!.tone" :title="countdown(e)!.label"><Clock aria-hidden="true" />{{ countdown(e)!.text }}</span>
            <span v-else class="asv-state" :class="`tone-${STATUS_TONE[e.status]}`">{{ STATUS_LABELS[e.status] }}</span>
          </td>
          <td data-label="来源">
            <span class="ac-source"><component :is="SOURCE_ICONS[sourceOf(e.source).kind]" aria-hidden="true" /><span>{{ sourceOf(e.source).label }}</span></span>
            <span v-if="sourceOf(e.source).inferred && sourceOf(e.source).conversation" class="ac-tag is-inferred" title="根据实例最近收到的消息推断，不是确定的聊天证据">来源推断</span>
          </td>
          <td class="asv-link-cell"><button type="button" class="asv-link" :aria-label="`查看：${e.summary}`" @click.stop="pick(e, $event)">查看</button></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
