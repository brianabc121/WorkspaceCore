// 按区间读取并自动加载全部分页（T-0169，规范 §6：不能只画前 100 条日程 / 任务）。
// 第一页到达就先显示，后续页按 next_cursor 依次自动加载；某一页失败或超过自动加载上限时停下，
// 页面显示“未加载完”并提供“继续加载”。写操作后的刷新会重新读取全部已加载的页。
import { computed, watchEffect, type ComputedRef } from 'vue'
import { useInfiniteQuery } from '@tanstack/vue-query'

export interface PageOf<T> { items: T[]; next_cursor?: string | null }

// 自动连续加载的页数上限（每页 100 条，即 2000 条）；超过后由用户点“继续加载”
export const AUTO_PAGES = 20

export function useAllPages<T>(options: {
  queryKey: ComputedRef<readonly unknown[]>
  enabled: ComputedRef<boolean>
  // 参数从 queryKey 取（而不是外面的响应式变量），刷新旧区间的缓存时不会读成新区间
  fetchPage: (cursor: string | null, signal: AbortSignal, queryKey: readonly unknown[]) => Promise<PageOf<T>>
  autoPages?: number
}) {
  const limit = options.autoPages ?? AUTO_PAGES
  const query = useInfiniteQuery({
    queryKey: options.queryKey,
    enabled: options.enabled,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal, queryKey }) => options.fetchPage(pageParam, signal, queryKey),
    getNextPageParam: (page: PageOf<T>) => page.next_cursor ?? undefined,
  })
  const pages = computed(() => query.data.value?.pages.length ?? 0)
  const items = computed<T[]>(() => query.data.value?.pages.flatMap((page) => page.items) ?? [])
  const capped = computed(() => !!query.hasNextPage.value && pages.value >= limit && !query.isFetching.value)
  watchEffect(() => {
    if (!options.enabled.value || !query.hasNextPage.value || query.isFetching.value || query.isError.value || query.isFetchNextPageError.value || pages.value >= limit) return
    void query.fetchNextPage()
  })
  // 还在继续加载后续页（第一页已到）
  const loadingMore = computed(() => options.enabled.value && pages.value > 0 && !!query.hasNextPage.value && !query.isFetchNextPageError.value && !capped.value)
  // 后续页失败或达到上限：已显示的只是一部分
  const incomplete = computed(() => options.enabled.value && pages.value > 0 && !!query.hasNextPage.value && (query.isFetchNextPageError.value || capped.value))
  const loadMore = () => query.fetchNextPage()
  return { query, items, pages, loadingMore, incomplete, capped, loadMore }
}
