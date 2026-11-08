<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { Building2, ChevronDown, ExternalLink, Menu, Moon, Sparkles, Sun } from '@lucide/vue'
import { canSee, navigation } from './app/navigation'
import { buildSidebar } from './app/sidebar'
import { useNavigationStore } from './stores/navigation'
import { useThemeStore } from './stores/theme'
import { useAuthStore } from './stores/auth'
import { useModulesStore } from './stores/modules'
import { errorMessage } from './services/api'
import HeaderSearch from './components/HeaderSearch.vue'
import HeaderNotifications from './components/HeaderNotifications.vue'
import ImHeaderBadges from './features/im/ImHeaderBadges.vue'
import AppDrawer from './components/AppDrawer.vue'
import CreateMenu from './components/CreateMenu.vue'
import AccountMenu from './components/AccountMenu.vue'
import BottomNav from './components/BottomNav.vue'
import ModuleDisabledPage from './pages/ModuleDisabledPage.vue'
import { useMediaQuery } from './composables/useMediaQuery'
import { useDrawerStore } from './stores/drawer'
import { useWorkspaceEvents } from './features/events/useWorkspaceEvents'
import { useCollapsedSections } from './composables/useCollapsedSections'

const route = useRoute()
const theme = useThemeStore()
const auth = useAuthStore()
const drawer = useDrawerStore()
const modules = useModulesStore()
useWorkspaceEvents()
const logoutError = ref('')
const loggingOut = ref(false)
type Entry = { path: string; title: string; section: string }
// 按角色过滤；停用的插拔式模块的入口不显示（T-0159）
const visibleNavigation = computed<Entry[]>(() => [
  ...navigation.filter((entry) => canSee(entry, auth) && modules.isEnabled(entry.module)),
  ...(auth.canManage ? [{ path: '/settings/audit', title: '审计日志', section: '管理' }] : []),
])
// 当前页面属于停用的模块：原地显示“功能未启用”（地址不变，再启用就恢复）
const disabledModule = computed(() => (auth.user && route.meta.module && !modules.isEnabled(route.meta.module) ? route.meta.module : null))
async function logout() {
  if (loggingOut.value) return
  loggingOut.value = true
  logoutError.value = ''
  try { await auth.logout() } catch (error) { logoutError.value = errorMessage(error) }
  finally { loggingOut.value = false }
}
const menuOpen = ref(false)
const menuToggle = ref<HTMLButtonElement | null>(null)
function closeMenu(event: KeyboardEvent) {
  if (event.key === 'Escape' && menuOpen.value) {
    menuOpen.value = false
    menuToggle.value?.focus()
  }
}
// 侧栏分组可以折叠（记在本机）；打开某一页时自动展开它所在的分组
const { collapsed, toggle: toggleSection } = useCollapsedSections()
// 分组与顺序按 owner 设置的侧栏布局（T-0161）；布局读不到时就是默认侧栏
const layout = useNavigationStore()
const sections = computed(() => buildSidebar(visibleNavigation.value, layout.layout, auth.currentWorkspace?.role).map((section, index) => ({ ...section, id: `nav-section-${index}` })))
// 路由还没解析完（START_LOCATION，matched 为空）时不算在任何分组里，免得把“工作空间”误展开
const currentEntry = computed(() => {
  if (!route.matched.length) return undefined
  for (const section of sections.value) {
    const item = section.items.find((entry) => entry.to === route.path)
    if (item) return { section: section.title, title: item.title }
  }
  return undefined
})
// 零代码页面（/p/:id）的标题取侧栏里的名字
const pageTitle = computed(() => String((route.path.startsWith('/p/') ? currentEntry.value?.title : undefined) ?? route.meta.title ?? 'My Workspace'))
watch(() => route.path, () => { menuOpen.value = false })
watch(() => currentEntry.value?.section, (section) => { if (section && collapsed.value.includes(section)) toggleSection(section) }, { immediate: true })
watch(pageTitle, (title) => { document.title = `${title} · My Workspace` }, { immediate: true })
// 手机上显示底部导航（T-0086）。按桌面宽度查询再取反：没有 matchMedia 的环境（jsdom）按桌面处理
const wide = useMediaQuery('(min-width: 761px)')
const bottomNav = computed(() => !wide.value && !!auth.user && !!auth.workspaceId)
const themeLabel = computed(() => (theme.theme === 'light' ? '切换到暗色主题' : '切换到亮色主题'))
// 独立页面（消息页，T-0095）不套侧栏、顶栏与 AI 抽屉，页面自己提供返回工作台的入口
const standalone = computed(() => !!route.meta.standalone && !!auth.user)
</script>

<template>
  <template v-if="standalone">
    <p v-if="auth.loading" role="status" class="standalone-status">正在连接工作空间…</p>
    <main v-else-if="disabledModule" id="main-content"><ModuleDisabledPage :module-key="disabledModule" /></main>
    <RouterView v-else />
  </template>
  <template v-else>
    <a class="skip-link" href="#main-content">跳到主要内容</a>
    <div class="workspace-shell" :class="{ 'has-bottom-nav': bottomNav }" @keydown="closeMenu">
      <aside v-if="auth.user" id="sidebar" class="sidebar" :class="{ 'is-open': menuOpen }">
        <RouterLink class="brand" to="/" aria-label="My Workspace 首页">
          <span class="brand-mark" aria-hidden="true">W</span>
          <span>My Workspace</span>
        </RouterLink>
        <div v-if="auth.currentWorkspace" class="workspace-switcher">
          <Building2 class="nav-icon" aria-hidden="true" />
          <select v-if="auth.workspaces.length > 1" aria-label="当前工作空间" :value="auth.workspaceId" @change="auth.selectWorkspace(($event.target as HTMLSelectElement).value)"><option v-for="workspace in auth.workspaces" :key="workspace.id" :value="workspace.id">{{ workspace.name }}</option></select>
          <span v-else class="workspace-name">{{ auth.currentWorkspace.name }}</span>
        </div>
        <nav aria-label="主导航" class="sidebar-nav">
          <div v-for="section in sections" :key="section.key" class="nav-section">
            <button class="nav-label" type="button" :aria-expanded="!collapsed.includes(section.title)" :aria-controls="section.id" @click="toggleSection(section.title)">
              <span>{{ section.title }}</span><ChevronDown class="nav-chevron" aria-hidden="true" />
            </button>
            <div v-show="!collapsed.includes(section.title)" :id="section.id" class="nav-items">
              <template v-for="item in section.items" :key="item.key">
                <a v-if="item.href" :href="item.href" target="_blank" rel="noopener noreferrer" class="nav-link">
                  <component :is="item.icon" class="nav-icon" aria-hidden="true" /><span>{{ item.title }}</span><ExternalLink class="nav-external" aria-hidden="true" /><span class="sr-only">（在新窗口打开）</span>
                </a>
                <RouterLink v-else-if="item.to" :to="item.to" class="nav-link" exact-active-class="is-active" :class="{ 'is-active': item.to === '/tasks' && route.path === '/tasks/timeline' }">
                  <component :is="item.icon" class="nav-icon" aria-hidden="true" /><span>{{ item.title }}</span>
                </RouterLink>
              </template>
            </div>
          </div>
        </nav>
        <div class="sidebar-footer">
          <AccountMenu :logging-out="loggingOut" @logout="logout" />
          <button class="shell-icon" type="button" :aria-label="themeLabel" :title="themeLabel" @click="theme.toggle"><Moon v-if="theme.theme === 'light'" aria-hidden="true" /><Sun v-else aria-hidden="true" /></button>
        </div>
      </aside>
      <div v-if="menuOpen" class="nav-backdrop" aria-hidden="true" @click="menuOpen = false"></div>
      <div class="main-shell">
        <header class="topbar">
          <div class="topbar-start">
            <button v-if="auth.user" ref="menuToggle" class="shell-icon menu-toggle" type="button" aria-label="切换导航" aria-controls="sidebar" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen"><Menu aria-hidden="true" /></button>
            <span class="breadcrumb"><span v-if="currentEntry" class="breadcrumb-section">{{ currentEntry.section }}<span aria-hidden="true" class="breadcrumb-sep">/</span></span><strong>{{ pageTitle }}</strong></span>
          </div>
          <div class="topbar-actions">
            <template v-if="auth.user">
              <HeaderSearch />
              <CreateMenu v-if="auth.workspaceId" :key="auth.workspaceId" />
              <ImHeaderBadges v-if="auth.canManage && auth.workspaceId" :key="auth.user.id + ':' + auth.workspaceId" />
              <HeaderNotifications />
              <button class="shell-icon topbar-ai" type="button" aria-controls="app-drawer" :aria-expanded="drawer.open" :aria-label="drawer.open ? '收起 AI 助手' : 'AI 助手'" :class="{ 'is-active': drawer.open }" @click="drawer.toggle()"><Sparkles aria-hidden="true" /><span class="topbar-label" aria-hidden="true">AI 助手</span></button>
            </template>
            <button v-else class="shell-icon" type="button" :aria-label="themeLabel" :title="themeLabel" @click="theme.toggle"><Moon v-if="theme.theme === 'light'" aria-hidden="true" /><Sun v-else aria-hidden="true" /></button>
          </div>
        </header>
        <main id="main-content" tabindex="-1">
          <p v-if="logoutError" role="alert" class="feedback">{{ logoutError }}</p>
          <p v-if="auth.loading" role="status">正在连接工作空间…</p>
          <ModuleDisabledPage v-else-if="disabledModule" :module-key="disabledModule" />
          <RouterView v-else />
        </main>
        <footer class="page-footer">My Workspace · 自托管个人工作台</footer>
      </div>
      <AppDrawer />
      <BottomNav v-if="bottomNav" :key="`${auth.user?.id}:${auth.workspaceId}:${auth.canManage}`" />
    </div>
  </template>
</template>
