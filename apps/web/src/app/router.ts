import { createRouter, createWebHistory, type RouteLocationNormalized, type RouteRecordRaw, type RouterHistory } from 'vue-router'
import { watch } from 'vue'
import type { Pinia } from 'pinia'
import { QueryClient } from '@tanstack/vue-query'
import { navigation, type NavItem } from './navigation'
import { moduleRoutes } from '../modules/registry'
import { useAuthStore } from '../stores/auth'
import { useModulesStore } from '../stores/modules'
import { isCreateIntentCleanup } from '../composables/useCreateIntent'
import { forgetDeployment } from '../features/agents/deploymentRequests'
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
import MailPage from '../pages/MailPage.vue'
import MailSettingsPage from '../pages/MailSettingsPage.vue'
import RunnerPage from '../pages/RunnerPage.vue'
import KnowledgePage from '../pages/KnowledgePage.vue'
import CalendarPage from '../pages/CalendarPage.vue'
import DeveloperPage from '../pages/DeveloperPage.vue'
import OperationsPage from '../pages/OperationsPage.vue'
import ImPage from '../pages/ImPage.vue'
import AgentsPage from '../pages/AgentsPage.vue'
import AgentServersPage from '../pages/AgentServersPage.vue'
import SecretsPage from '../pages/SecretsPage.vue'
import AiPage from '../pages/AiPage.vue'
import AppCenterPage from '../pages/AppCenterPage.vue'
import NavigationSettingsPage from '../pages/NavigationSettingsPage.vue'

// 知识库的子页面按需加载（编辑器、图谱的依赖较大）。/knowledge 是知识总览（T-0173），完整图谱在 /knowledge/graph。
// 旧链接：?linked_resource_id=、?create=1（书签）转到“外部文档”；?focus=<知识库>（原来的图谱首页）转到完整图谱，其余参数照带
const knowledgeRoutes: RouteRecordRaw[] = [
  { path: '', component: () => import('../features/kb/KbOverviewView.vue'), meta: { title: '知识总览', kbSection: 'overview' },
    beforeEnter: (to) => {
      if (to.query.linked_resource_id !== undefined || to.query.create !== undefined) return { path: '/knowledge/docs', query: to.query }
      if (to.query.focus !== undefined) return { path: '/knowledge/graph', query: to.query, hash: to.hash }
      return true
    } },
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

// 侧栏项的标题、说明与角色要求（admin = owner / admin，owner = 只有 owner）
const navMeta = (item: NavItem) => ({ title: item.title, description: item.description, admin: item.minRole === 'admin', owner: item.minRole === 'owner' })

// 宽版内容区：任务、项目、日历、邮件（四页改造），开发者、运维（二批四页改造，T-0176 / T-0177），
// 智能体服务器、OpenClaw 智能体（T-0181 / T-0182）。列表与详情并列；其他页面宽度不变。
// 知识库是独立页面，自己的布局；早报是模块，在模块路由里声明
// 管理区八页（T-0189，方案 docs/v2/ai-admin-redesign）也是宽页面；新闻早报设置在模块路由的 meta 里登记
// 审计日志不在侧栏导航里，它的路由在下面单独登记 wide
const ADMIN_PAGES = ['/ai', '/settings', '/settings/integrations', '/settings/security', '/settings/mail', '/agents/secrets']
const WIDE_PAGES = new Set(['/tasks', '/projects', '/calendar', '/mail', '/developer', '/operations', '/agent-servers', '/agents', '/runner', ...ADMIN_PAGES])

// 旧链接（T-0147 的两个标签）：/agents?tab=servers&server=… 换成独立的服务器页（只带有效的 server 与 range），
// /agents?tab=instances 去掉 tab、保留其余参数（例如 instance）。都用 replace，后退不会回到旧地址
const HOST_RANGES = ['24h', '7d', '30d']
export function legacyAgentsRedirect(to: RouteLocationNormalized) {
  if (to.path !== '/agents' || to.query.tab === undefined) return null
  if (to.query.tab === 'servers') {
    const query: Record<string, string> = {}
    if (typeof to.query.server === 'string' && to.query.server) query.server = to.query.server
    if (typeof to.query.range === 'string' && HOST_RANGES.includes(to.query.range)) query.range = to.query.range
    return { path: '/agent-servers', query, hash: to.hash, replace: true }
  }
  const query = { ...to.query }
  delete query.tab
  return { path: '/agents', query, hash: to.hash, replace: true }
}

export function createAppRouter(pinia: Pinia, history: RouterHistory = createWebHistory(), queryClient = new QueryClient()) {
  const auth = useAuthStore(pinia)
  const modules = useModulesStore(pinia)
  const router = createRouter({
    history,
    routes: [
      { path: '/login', component: AuthPage, meta: { title: '登录', public: true } },
      { path: '/setup', component: AuthPage, meta: { title: '首次安装', public: true } },
      { path: '/connection-error', component: ConnectionErrorPage, meta: { title: '连接失败', public: true } },
      { path: '/forbidden', component: UnauthorizedPage, meta: { title: '无权访问' } },
      { path: '/settings/audit', component: AuditPage, meta: { title: '审计日志', admin: true, wide: true } },
      ...navigation.filter((item) => !item.module).map((item) => ({
        path: item.path,
        // 知识库（T-0099）是带子页面的独立页面：总览、图谱、笔记、编辑器、知识库主页、变更记录、外部文档
        ...(item.path === '/knowledge' ? { children: knowledgeRoutes } : {}),
        component: ({ '/': HomePage, '/tasks': TasksPage, '/projects': ProjectsPage, '/knowledge': KnowledgePage, '/calendar': CalendarPage, '/developer': DeveloperPage, '/operations': OperationsPage, '/im': ImPage, '/agents': AgentsPage, '/agent-servers': AgentServersPage, '/agents/secrets': SecretsPage, '/ai': AiPage, '/settings': SettingsPage, '/settings/modules': AppCenterPage, '/settings/navigation': NavigationSettingsPage, '/settings/security': SecurityPage, '/settings/integrations': IntegrationsPage, '/mail': MailPage, '/settings/mail': MailSettingsPage, '/runner': RunnerPage } as Record<string, typeof HomePage>)[item.path] ?? PlaceholderPage,
        // 消息页（T-0095）、知识库（T-0099）是不带工作台侧栏和顶栏的独立页面
        meta: { ...navMeta(item), standalone: item.path === '/im' || item.path === '/knowledge', wide: WIDE_PAGES.has(item.path) },
      })),
      // 插拔式模块的页面（T-0159）：按需加载；标题、说明与角色要求取自模块同一地址的侧栏项
      ...moduleRoutes.map((route) => {
        const item = navigation.find((entry) => entry.path === route.path)
        return { ...route, meta: { ...(item ? navMeta(item) : {}), ...route.meta } } as RouteRecordRaw
      }),
      // 侧栏里的零代码页面（T-0161）：仪表盘与嵌入页；标题取侧栏布局里的名字
      { path: '/p/:id', component: () => import('../pages/CustomPage.vue'), meta: { title: '页面' } },
      // 项目页（T-0122）：打开项目先看 README
      { path: '/projects/:id', component: () => import('../pages/ProjectPage.vue'), meta: { title: '项目', description: '项目的导览：成员、沟通、任务概况与约定。' } },
      // 项目里的 Issues（T-0124）：通知与搜索链到 /issues/:issue，正文里的 #编号 链到项目内编号
      { path: '/issues/:issue', component: () => import('../pages/IssuePage.vue'), meta: { title: 'Issue', description: '项目里的议题：正文、评论与时间线。' } },
      { path: '/projects/:project/issues/:number(\\d+)', component: () => import('../pages/IssuePage.vue'), meta: { title: 'Issue', description: '项目里的议题：正文、评论与时间线。' } },
      // 任务时间轴（T-0105）：任务页的一种显示方式，地址里带 project / date / scale / group / task
      { path: '/tasks/timeline', component: () => import('../pages/TaskTimelinePage.vue'), meta: { title: '任务时间轴', description: '每个执行者一条轨道：开始、结束、是否还在执行、消耗了多少 token。' } },
      // Notification links for AI approvals (OC-1 contract §6) use /approvals?id=…
      { path: '/approvals', redirect: (to) => ({ path: '/ai', query: to.query }) },
      { path: '/:pathMatch(.*)*', component: NotFoundPage, meta: { title: '页面未找到' } },
    ],
    scrollBehavior: (to, from) => (isCreateIntentCleanup(to, from) ? false : { top: 0 }),
  })
  router.beforeEach(async (to) => {
    if (to.path === '/connection-error') return true
    const legacy = legacyAgentsRedirect(to)
    if (legacy) return legacy
    try { await auth.initialize() }
    catch { return { path: '/connection-error', query: { redirect: to.fullPath } } }
    if (auth.setupRequired) return to.path === '/setup' ? true : '/setup'
    if (!auth.user) return to.path === '/login' ? true : { path: '/login', query: { redirect: to.fullPath } }
    if (to.path === '/setup' || to.path === '/login') return '/'
    if (to.meta.admin && !auth.canManage) return '/forbidden'
    if (to.meta.owner && !auth.isOwner) return '/forbidden'
    // 模块页面先等启用状态读完，停用时 App.vue 原地显示“功能未启用”，不会先闪出页面
    if (to.meta.module) await modules.ready()
    return true
  })
  watch(() => auth.user?.id, (user, previous) => {
    if (user !== previous) queryClient.clear()
    // 登出或换了用户：清掉浏览器里记着的未完成部署请求（T-0182）；刚打开页面（之前没有用户）时保留，用来刷新后找回
    if (previous && user !== previous) forgetDeployment()
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
