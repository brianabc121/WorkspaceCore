import { inject, ref, type InjectionKey, type Ref } from 'vue'

// 组件标题栏右侧的小标记（T-0164，首页效果图）：任务进展的“需要你关注 N 项”、新闻速览的“AI 整理”与时间。
// WidgetFrame 提供，组件自己设置；不在 WidgetFrame 里时（单独渲染、测试）设置无效
export type WidgetBadge = { text: string; tone: 'warning' | 'accent' }
export type WidgetHeader = { badge?: WidgetBadge | null; meta?: string | null }
export const widgetHeaderKey: InjectionKey<Ref<WidgetHeader>> = Symbol('widget-header')
export function useWidgetHeader() {
  return inject(widgetHeaderKey, ref<WidgetHeader>({}))
}
