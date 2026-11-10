<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { errorMessage } from '../../services/api'
import { formatTime } from '../time'
import { useActivity, type ActivityItem } from './api'
import { SYNC_REASONS } from './viewModel'

// 模块的操作记录（T-0211，方案第 3.4 节）：启用 / 停用、同步结果与 owner 重试，按工作空间与模块 key 精确筛选。
// moduleKey 为 null 时列出所有模块（含已移除模块的历史）。只读：不提供修改或删除。
const props = defineProps<{ moduleKey: string | null; names?: ReadonlyMap<string, string>; label?: string }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const activity = useActivity(scope, () => props.moduleKey)
const items = computed(() => [...new Map((activity.data.value?.pages ?? []).flatMap(page => page.items).map(item => [item.id, item])).values()])

function details(item: ActivityItem): string[] {
  const meta = (item.metadata ?? {}) as Record<string, unknown>
  const parts: string[] = []
  if (props.moduleKey === null && item.resource_id) parts.push(props.names?.get(item.resource_id) ?? `${item.resource_id}（已移除）`)
  if (typeof meta.target_enabled === 'boolean') parts.push(`目标：${meta.target_enabled ? '启用' : '停用'}`)
  if (meta.trigger === 'retry') parts.push('手动重试')
  if (typeof meta.reason === 'string' && meta.reason in SYNC_REASONS) parts.push(SYNC_REASONS[meta.reason as keyof typeof SYNC_REASONS])
  if (typeof meta.state_version === 'number') parts.push(`状态版本 ${meta.state_version}`)
  return parts
}
const tone = (action: string) => (action.endsWith('.failed') ? 'red' : action.endsWith('.unknown') ? 'amber' : action.endsWith('.succeeded') ? 'green' : 'blue')
</script>

<template>
  <section class="apc-activity" :aria-label="label ?? '操作记录'">
    <p v-if="activity.isPending.value" role="status" class="ws-state-line">正在加载操作记录…</p>
    <div v-else-if="activity.error.value && !items.length" class="ws-state-line"><p role="alert">{{ errorMessage(activity.error.value) }}</p><button type="button" class="secondary-button" @click="activity.refetch()">重试</button></div>
    <p v-else-if="!items.length" class="ws-state-line">还没有操作记录。</p>
    <ol v-else class="apc-activity-list">
      <li v-for="item in items" :key="item.id">
        <span class="apc-dot" :class="`tone-${tone(item.action)}`" aria-hidden="true"></span>
        <div class="apc-activity-body">
          <strong>{{ item.action_label || item.action }}</strong>
          <span class="apc-activity-meta"><time :datetime="item.created_at">{{ formatTime(item.created_at) }}</time> · {{ item.actor_id ? item.actor_label || '成员' : '系统' }}</span>
          <span v-if="details(item).length" class="apc-activity-meta">{{ details(item).join(' · ') }}</span>
        </div>
      </li>
    </ol>
    <footer v-if="items.length" class="asv-foot ag-foot">
      <span>已显示 {{ items.length }} 条<template v-if="activity.error.value"> · 刷新失败，显示的是上次取回的记录</template></span>
      <button v-if="activity.hasNextPage.value" type="button" class="secondary-button" :disabled="activity.isFetchingNextPage.value" @click="activity.fetchNextPage()">加载更多记录</button>
    </footer>
    <p class="form-help">只记录模块的启用、停用、外部配置同步与重试，不是模块内的全部业务日志；完整记录见审计日志。</p>
  </section>
</template>
