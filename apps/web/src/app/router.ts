import { createRouter, createWebHistory, type RouteRecordRaw, type RouterHistory } from 'vue-router'
import { watch } from 'vue'
import type { Pinia } from 'pinia'
import { QueryClient } from '@tanstack/vue-query'
import { adminOnly, navigation, ownerOnly } from './navigation'
import { useAuthStore } from '../stores/auth'
import { isCreateIntentCleanup } from '../composables/useCreateIntent'
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
import OperationsPage from '../pages/OperationsPage.vue'
import AutomationsPage from '../pages/AutomationsPage.vue'
import ImPage from '../pages/ImPage.vue'
import AgentsPage from '../pages/AgentsPage.vue'
import SecretsPage from '../pages/SecretsPage.vue'
import AiPage from '../pages/AiPage.vue'

// 知识库的子页面按需加载（编辑器、图谱的依赖较大）。旧的书签链接（?linked_resource_id=、?create=1）转到“外部文档”
const knowledgeRoutes: RouteRecordRaw[] = [
  { path: '', component: () => import('../features/kb/KbGraphView.vue'), meta: { title: '知识库', kbSection: 'graph' },
    beforeEnter: (to) => (to.query.linked_resource_id !== undefined || to.query.create !== undefined ? { path: '/knowledge/docs', query: to.query } : true) },
  { path: 'graph', component: () => import('../features/kb/KbGraphView.vue'), meta: { title: '知识图谱', kbSection: 'graph', kbGraphOnly: true } },
  { path: 'notes', component: () => import('../features/kb/KbNotesView.vue'), meta: { title: '笔记', kbSection: 'notes' } },
  { path: 'notes/new', component: () => import('../features/kb/KbEditorView.vue'), meta: { title: '新建笔记', kbSection: 'notes' } },
  { path: 'notes/:id', component: () => import('../features/kb/KbNoteView.vue'), meta: { title: '笔记', kbSection: 'notes' } },
  { path: 'notes/:id/edit', component: () => import('../features/kb/KbEditorView.vue'), meta: { title: '编辑笔记', kbSection: 'notes' } },
  { path: 'spaces/:id?', component: () => import('../features/kb/KbSpaceView.vue'), meta: { title: '项目知识库', kbSection: 'spaces' } },
  { path: 'agents/:id?', component: () => import('../features/kb/KbAgentView.vue'), meta: { title: '智能体知识库', kbSection: 'agents' } },
  { path: 'changes', component: () => import('../features/kb/KbChangesView.vue'), meta: { title: '变更记录', kbSection: 'changes' } },
  { path: 'docs', component: () => import('../features/kb/KbDocsView.vue'), meta: { title: '外部文档', kbSection: 'docs' } },
]

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
        // 知识库（T-0099）是带子页面的独立页面：图谱、笔记、编辑器、知识库主页、变更记录、外部文档
        ...(item.path === '/knowledge' ? { children: knowledgeRoutes } : {}),
        component: ({ '/': HomePage, '/tasks': TasksPage, '/projects': ProjectsPage, '/knowledge': KnowledgePage, '/calendar': CalendarPage, '/developer': DeveloperPage, '/operations': OperationsPage, '/automations': AutomationsPage, '/im': ImPage, '/agents': AgentsPage, '/agents/secrets': SecretsPage, '/ai': AiPage, '/settings': SettingsPage, '/settings/security': SecurityPage, '/settings/integrations': IntegrationsPage } as Record<string, typeof HomePage>)[item.path] ?? PlaceholderPage,
        // 消息页（T-0095）、知识库（T-0099）是不带工作台侧栏和顶栏的独立页面
        meta: { title: item.title, description: item.description, admin: adminOnly.has(item.path), owner: ownerOnly.has(item.path), standalone: item.path === '/im' || item.path === '/knowledge' },
      })),
      // Notification links for AI approvals (OC-1 contract §6) use /approvals?id=…
      { path: '/approvals', redirect: (to) => ({ path: '/ai', query: to.query }) },
      { path: '/:pathMatch(.*)*', component: NotFoundPage, meta: { title: '页面未找到' } },
    ],
    scrollBehavior: (to, from) => (isCreateIntentCleanup(to, from) ? false : { top: 0 }),
  })
  router.beforeEach(async (to) => {
    if (to.path === '/connection-error') return true
    try { await auth.initialize() }
    catch { return { path: '/connection-error', query: { redirect: to.fullPath } } }
    if (auth.setupRequired) return to.path === '/setup' ? true : '/setup'
    if (!auth.user) return to.path === '/login' ? true : { path: '/login', query: { redirect: to.fullPath } }
    if (to.path === '/setup' || to.path === '/login') return '/'
    if (to.meta.admin && !auth.canManage) return '/forbidden'
    if (to.meta.owner && !auth.isOwner) return '/forbidden'
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
    if ((router.currentRoute.value.meta.admin && !auth.canManage) || (router.currentRoute.value.meta.owner && !auth.isOwner)) void router.replace('/forbidden')
  })
  return router
}
