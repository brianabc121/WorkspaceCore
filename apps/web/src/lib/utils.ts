import { computed, type ComputedRef } from 'vue'
import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

// shadcn-vue 组件用的工具（组件源码来自 unovue/shadcn-vue v2.9.0，见 docs/decisions/2026-11-01-shadcn-vue-and-drawer.md）。
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

// 代替 @vueuse/core 的 reactiveOmit：去掉 class 等只给外层用的属性，其余原样转给 reka-ui 组件。
export function omitProps<T extends object, K extends keyof T>(props: T, ...keys: K[]): ComputedRef<Omit<T, K>> {
  return computed(() => {
    const rest = { ...props }
    for (const key of keys) delete rest[key]
    return rest as Omit<T, K>
  })
}
