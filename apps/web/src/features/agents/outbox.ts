import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'

// 实例的发件记录（T-0118，AGENT_MESSAGING_PLAN 4.6）：智能体经 im_send 主动发的消息与被拒收的自动回复。
// 键以 'agents' 开头，跟着 agents 主题一起刷新；有排队、定时或确认中的消息时每 5 秒轮询一次。
export type OutboxItem = Schema['ImOutboxItem']
export type OutboxStatus = OutboxItem['status']

export function outboxKey(user: string | undefined, scope: string, instanceId: string) {
  return ['agents', user, scope, 'outbox', instanceId] as const
}

export const PENDING: OutboxStatus[] = ['scheduled', 'queued', 'sending', 'unknown']

export function useAgentOutbox(scope: string, instanceId: () => string) {
  const auth = useAuthStore(), user = auth.user?.id
  const header = { 'X-Workspace-Id': scope }
  return useQuery({
    queryKey: computed(() => outboxKey(user, scope, instanceId())),
    enabled: computed(() => user === auth.user?.id && scope === auth.workspaceId && auth.isOwner),
    queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/agents/instances/{instance_id}/outbox', { params: { header, path: { instance_id: instanceId() } }, signal })),
    refetchInterval: query => query.state.data?.items.some(i => PENDING.includes(i.status)) ? 5000 : false,
  })
}

export const OUTBOX_STATUS: Record<OutboxStatus, string> = {
  scheduled: '定时', queued: '排队中', sending: '发送中', sent: '已发出', rejected: '被拒收', failed: '没发出', unknown: '确认中', cancelled: '已取消',
}
export const OUTBOX_SOURCE: Record<OutboxItem['source'], string> = { tool: '主动发送', reply_retry: '限流后补发', wakeup: '唤醒后发送' }
export const OUTBOX_KIND: Record<OutboxItem['kind'], string> = { text: '文字', image: '图片', file: '文件', video: '视频' }

export function targetLabel(target: string): string {
  const [kind, id] = target.split(':')
  return kind === 'group' ? `群 ${id}` : `私聊 ${id}`
}
