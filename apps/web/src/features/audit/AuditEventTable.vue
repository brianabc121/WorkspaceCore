<script setup lang="ts">
import ManagementSection from '../../components/management/ManagementSection.vue'
import DataState from '../../components/DataState.vue'
import { actorText, breakable, eventIcon, eventTitle, isKnownEvent, objectText, rowTime } from './display'
import type { AuditEntry } from './queries'

// 事件记录表（T-0198，效果图 08）：时间、操作者、事件、对象、详情。只显示已加载的条数（接口不给全量总数）；
// “加载更多”用同一组条件的 next_cursor。没有“结果”一列：审计记录没有通用的成功 / 失败字段。
defineProps<{
  entries: AuditEntry[]; loading: boolean; error: unknown; hasMore: boolean; loadingMore: boolean; selectedId: string; filtered: boolean
}>()
const emit = defineEmits<{ select: [id: string, opener: HTMLElement | null]; more: []; retry: [] }>()
function open(entry: AuditEntry, event: Event) {
  const row = (event.currentTarget as HTMLElement | null)?.closest('tr')
  emit('select', entry.id, row?.querySelector<HTMLElement>('.au-open') ?? null)
}
const label = (entry: AuditEntry) => `${eventTitle(entry)}，${rowTime(entry.created_at)}`
</script>

<template>
  <ManagementSection title="事件记录" class="au-events">
    <template #title>事件记录 <span class="au-count">当前已加载 {{ entries.length }} 条</span></template>
    <DataState
      :loading="loading" :error="error" :empty="!entries.length" empty-text="暂无审计记录" :stale="!!entries.length"
      @retry="emit('retry')"
    />
    <p v-if="!loading && !error && !entries.length && filtered" class="form-help au-empty-hint">可以换一个时间范围，或清除筛选后再看。</p>
    <table v-if="entries.length" class="ws-table asv-table au-table">
      <caption class="sr-only">审计事件，新的在前</caption>
      <thead><tr><th scope="col">时间</th><th scope="col">操作者</th><th scope="col">事件</th><th scope="col">对象</th><th scope="col">操作</th></tr></thead>
      <tbody>
        <tr
          v-for="entry in entries" :key="entry.id" :data-entry="entry.id" :aria-label="label(entry)"
          :aria-selected="entry.id === selectedId" @click="open(entry, $event)"
        >
          <td data-label="时间" class="au-time"><time :datetime="entry.created_at">{{ rowTime(entry.created_at) }}</time></td>
          <td data-label="操作者" class="au-actor">
            <span v-if="actorText(entry).resolved" :class="{ 'au-system': entry.actor_id === null }">{{ actorText(entry).name }}</span>
            <span v-else class="au-id" :title="entry.actor_id ?? undefined">{{ actorText(entry).name }}</span>
          </td>
          <th scope="row" class="au-event">
            <span class="au-event-line">
              <component :is="eventIcon(entry.action)" class="au-event-icon" aria-hidden="true" />
              <span class="au-event-text">
                <span v-if="isKnownEvent(entry)" class="au-event-title">{{ entry.action_label }}</span>
                <code v-else class="au-event-title au-raw"><template v-for="(part, i) in breakable(entry.action)" :key="i">{{ part }}<wbr /></template></code>
                <code v-if="isKnownEvent(entry)" class="asv-sub au-action"><template v-for="(part, i) in breakable(entry.action)" :key="i">{{ part }}<wbr /></template></code>
                <span v-else class="asv-sub">未收录的事件，按原名显示</span>
              </span>
            </span>
          </th>
          <td data-label="对象" class="au-object">
            <template v-if="objectText(entry)">
              <span class="au-object-name" :title="objectText(entry)!.title ?? undefined">{{ objectText(entry)!.name }}</span>
              <span v-if="objectText(entry)!.tag === 'current'" class="au-tag">当前名称</span>
              <span v-else-if="objectText(entry)!.tag === 'recorded'" class="au-tag is-muted">记录时</span>
            </template>
            <span v-else class="au-none">无关联对象</span>
          </td>
          <td class="asv-link-cell">
            <button type="button" class="asv-link au-open" :aria-current="entry.id === selectedId ? 'true' : undefined" @click.stop="open(entry, $event)">详情</button>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-if="entries.length" class="au-foot">
      <span class="form-help">新的在前</span>
      <button v-if="hasMore" type="button" class="secondary-button au-more" :disabled="loadingMore" @click="emit('more')">{{ loadingMore ? '正在加载…' : '加载更多' }}</button>
      <span v-else-if="!error" class="form-help">已加载全部符合条件的记录</span>
    </div>
  </ManagementSection>
</template>
