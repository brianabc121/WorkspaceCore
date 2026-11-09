import { computed, ref, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { FILTERS, aggregateKey, forgetMailContent, useMailAggregate, type MailAccountState, type MailFilter, type MailFilters, type MailItem, type SelectedMail } from './api'
import { mergeItems } from './format'

// 邮件页的作用域（T-0171，四页改造规范第 7 节）：默认全部邮箱（accountId 为 null）+ 全部邮件，
// 不默认选第一个邮箱；只有显式的深链（?account=）才直接打开单个邮箱。
// 换邮箱、分类、筛选、搜索：清空选中的邮件与分页（新的查询键从第一批开始），旧请求被取消、迟到的响应不回填。
// 搜索词在换邮箱 / 分类时保留，作用到新的范围上（列表、搜索、分类、加载更多、刷新始终是同一个范围）。
export function useMailScope(scope: string, options: { initialAccount: string | null; canLeave: () => boolean }) {
  const auth = useAuthStore(), cache = useQueryClient(), user = auth.user?.id
  const accountId = ref<string | null>(options.initialAccount)
  const label = ref('')
  const filter = ref<MailFilter>('all')
  const query = ref('')
  const selected = ref<SelectedMail | null>(null)
  const filters = computed<MailFilters>(() => ({
    accountId: accountId.value,
    label: label.value,
    q: [query.value, FILTERS.find((f) => f.id === filter.value)?.term ?? ''].filter(Boolean).join(' '),
  }))
  const list = useMailAggregate(scope, filters)
  const pages = computed(() => list.data.value?.pages ?? [])
  const items = computed(() => mergeItems(pages.value))
  const latest = computed(() => pages.value.at(-1) ?? null)
  // 有依据的未读数：任意一批读到过就记下；每个邮箱最近一次读取的状态（左栏显示限流、暂时读不到）。只在内存里
  const unread = ref<Record<string, number>>({})
  const states = ref<Record<string, MailAccountState>>({})
  watch(pages, (list) => {
    for (const page of list) {
      for (const state of page.accounts) {
        states.value[state.account_id] = state
        if (state.unread !== null) unread.value[state.account_id] = state.unread
      }
    }
  }, { immediate: true })

  function change(apply: () => void) {
    if (!options.canLeave()) return false
    selected.value = null
    apply()
    return true
  }
  return {
    accountId, label, filter, query, selected, filters, list, pages, items, latest, unread, states,
    setAccount: (id: string | null) => (id === accountId.value && !selected.value ? true : change(() => { accountId.value = id })),
    setLabel: (id: string) => change(() => { label.value = id }),
    setFilter: (id: MailFilter) => change(() => { filter.value = id }),
    search: (text: string) => change(() => { query.value = text.trim() }),
    select: (item: MailItem) => { selected.value = { accountId: item.account_id, messageId: item.id, threadId: item.thread_id } },
    // 刷新：同一个范围从第一批重新读（丢掉旧游标），正在看的会话也重新读
    async refresh() {
      await Promise.all([
        cache.resetQueries({ queryKey: aggregateKey(user, scope, filters.value) }),
        cache.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'mail' && q.queryKey[1] === user && q.queryKey[2] === scope && ['thread', 'message', 'accounts'].includes(String(q.queryKey[3])) }),
      ])
    },
    // 发出回复后重新读正在看的会话
    async refreshOpened() {
      await cache.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'mail' && q.queryKey[1] === user && q.queryKey[2] === scope && ['thread', 'message'].includes(String(q.queryKey[3])) })
    },
    forget() { selected.value = null; forgetMailContent(cache, user, scope) },
  }
}
