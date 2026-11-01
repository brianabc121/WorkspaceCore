import { onScopeDispose, ref } from 'vue'

// 视口媒体查询；没有 matchMedia 的环境（jsdom）按“匹配”处理。
export function useMediaQuery(query: string) {
  const media = typeof window.matchMedia === 'function' ? window.matchMedia(query) : null
  const matches = ref(media ? media.matches : true)
  const update = (event: MediaQueryListEvent) => { matches.value = event.matches }
  media?.addEventListener('change', update)
  onScopeDispose(() => media?.removeEventListener('change', update))
  return matches
}

// 规范 §8.1：桌面上右侧抽屉作为一栏停靠在页面旁；窄屏上改为覆盖页面的侧滑面板
export const DRAWER_DOCKED_QUERY = '(min-width: 1280px)'
