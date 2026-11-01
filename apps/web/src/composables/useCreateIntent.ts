import { onScopeDispose, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// 顶栏“创建”菜单（规范 §8.1）跳到对应模块页并带上 ?create=1，模块页用它打开自己已有的新建表单，
// 新建流程（幂等键、结果未知的处理、权限提示）只有一份。参数用过就从地址里去掉：刷新、后退不会再弹出表单。
export const CREATE_QUERY = 'create'

// 'wait'：页面还在加载判断条件（例如知识来源）；false：现在不能新建，只去掉参数，原因由页面自己显示。
export type CreateReadiness = boolean | 'wait'

export function useCreateIntent(readiness: () => CreateReadiness, open: () => void) {
  const route = useRoute(), router = useRouter()
  let handling = false, active = true
  onScopeDispose(() => { active = false })
  watch([() => route.query[CREATE_QUERY], readiness], async ([flag, state]) => {
    if (flag === undefined || state === 'wait' || handling) return
    handling = true
    const query = { ...route.query }
    delete query[CREATE_QUERY]
    try { await router.replace({ query, hash: route.hash }) } finally { handling = false }
    if (active && readiness() === true) open()
  }, { immediate: true })
}

// 只去掉 create 参数的那次跳转不滚动页面（router 的 scrollBehavior 用）。
export function isCreateIntentCleanup(to: { path: string; query: Record<string, unknown> }, from: { path: string; query: Record<string, unknown> }) {
  return to.path === from.path && from.query[CREATE_QUERY] !== undefined && to.query[CREATE_QUERY] === undefined
}
