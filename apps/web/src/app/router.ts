import { createRouter, createWebHistory, type RouterHistory } from 'vue-router'
import { watch } from 'vue'
import type { Pinia } from 'pinia'
import { QueryClient } from '@tanstack/vue-query'
import { navigation } from './navigation'
import { useAuthStore } from '../stores/auth'
import AuthPage from '../pages/AuthPage.vue'
import ConnectionErrorPage from '../pages/ConnectionErrorPage.vue'
import UnauthorizedPage from '../pages/UnauthorizedPage.vue'
import SettingsPage from '../pages/SettingsPage.vue'
import SecurityPage from '../pages/SecurityPage.vue'
import AuditPage from '../pages/AuditPage.vue'
import HomePage from '../pages/HomePage.vue'
import PlaceholderPage from '../pages/PlaceholderPage.vue'
import NotFoundPage from '../pages/NotFoundPage.vue'
import TasksPage from '../pages/TasksPage.vue'
import ProjectsPage from '../pages/ProjectsPage.vue'
import IntegrationsPage from '../pages/IntegrationsPage.vue'
import KnowledgePage from '../pages/KnowledgePage.vue'
import CalendarPage from '../pages/CalendarPage.vue'
import DeveloperPage from '../pages/DeveloperPage.vue'

export function createAppRouter(pinia: Pinia, history: RouterHistory = createWebHistory(), queryClient = new QueryClient()) {
  const auth = useAuthStore(pinia)
  const router = createRouter({
    history,
    routes: [
      { path: '/login', component: AuthPage, meta: { title: '登录', public: true } },
      { path: '/setup', component: AuthPage, meta: { title: '首次安装', public: true } },
      { path: '/connection-error', component: ConnectionErrorPage, meta: { title: '连接失败', public: true } },
      { path: '/forbidden', component: UnauthorizedPage, meta: { title: '无权访问' } },
      { path: '/settings/audit', component: AuditPage, meta: { title: '审计日志', admin: true } },
      ...navigation.map((item) => ({
        path: item.path,
        component: ({ '/': HomePage, '/tasks': TasksPage, '/projects': ProjectsPage, '/knowledge': KnowledgePage, '/calendar': CalendarPage, '/developer': DeveloperPage, '/settings': SettingsPage, '/settings/security': SecurityPage, '/settings/integrations': IntegrationsPage } as Record<string, typeof HomePage>)[item.path] ?? PlaceholderPage,
        meta: { title: item.title, description: item.description, admin: item.path === '/settings/integrations' },
      })),
      { path: '/:pathMatch(.*)*', component: NotFoundPage, meta: { title: '页面未找到' } },
    ],
    scrollBehavior: () => ({ top: 0 }),
  })
  router.beforeEach(async (to) => {
    if (to.path === '/connection-error') return true
    try { await auth.initialize() }
    catch { return { path: '/connection-error', query: { redirect: to.fullPath } } }
    if (auth.setupRequired) return to.path === '/setup' ? true : '/setup'
    if (!auth.user) return to.path === '/login' ? true : { path: '/login', query: { redirect: to.fullPath } }
    if (to.path === '/setup' || to.path === '/login') return '/'
    if (to.meta.admin && !auth.canManage) return '/forbidden'
    return true
  })
  watch(() => auth.user?.id, (user, previous) => {
    if (user !== previous) queryClient.clear()
    if (!user && auth.initialized && router.currentRoute.value.matched.length && !router.currentRoute.value.meta.public) {
      void router.replace({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
    }
  }, { flush: 'sync' })
  watch(() => auth.workspaceId, () => {
    queryClient.removeQueries({ predicate: (query) => query.queryKey[0] === 'audit' })
    if (router.currentRoute.value.meta.admin && !auth.canManage) void router.replace('/forbidden')
  })
  return router
}
