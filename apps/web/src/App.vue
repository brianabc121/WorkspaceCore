<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { adminOnly, navigation, ownerOnly } from './app/navigation'
import { useThemeStore } from './stores/theme'
import { useAuthStore } from './stores/auth'
import { errorMessage } from './services/api'
import HeaderSearch from './components/HeaderSearch.vue'
import HeaderNotifications from './components/HeaderNotifications.vue'
import ImHeaderBadges from './features/im/ImHeaderBadges.vue'
import AppDrawer from './components/AppDrawer.vue'
import CreateMenu from './components/CreateMenu.vue'
import { useDrawerStore } from './stores/drawer'
import { useWorkspaceEvents } from './features/events/useWorkspaceEvents'

const route = useRoute()
const theme = useThemeStore()
const auth = useAuthStore()
const drawer = useDrawerStore()
useWorkspaceEvents()
const logoutError = ref('')
const loggingOut = ref(false)
const visibleNavigation = computed(() => (auth.canManage ? [...navigation, { path: '/settings/audit', title: '审计日志', section: '管理' }] : navigation.filter((entry) => !adminOnly.has(entry.path))).filter((entry) => auth.isOwner || !ownerOnly.has(entry.path)))
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
const sections = [...new Set(navigation.map((item) => item.section))]
const pageTitle = computed(() => String(route.meta.title ?? 'My Workspace'))
watch(() => route.path, () => { menuOpen.value = false })
watch(pageTitle, (title) => { document.title = `${title} · My Workspace` }, { immediate: true })
</script>

<template>
  <a class="skip-link" href="#main-content">跳到主要内容</a>
  <div class="workspace-shell" @keydown="closeMenu">
    <aside v-if="auth.user" id="sidebar" class="sidebar" :class="{ 'is-open': menuOpen }">
      <RouterLink class="brand" to="/" aria-label="My Workspace 首页">
        <span class="brand-mark" aria-hidden="true">W</span>
        <span>My Workspace<small>你的个人工作空间</small></span>
      </RouterLink>
      <nav aria-label="主导航">
        <div v-for="section in sections" :key="section" class="nav-section">
          <p class="nav-label">{{ section }}</p>
          <RouterLink v-for="item in visibleNavigation.filter((entry) => entry.section === section)" :key="item.path" :to="item.path" class="nav-link" exact-active-class="is-active">
            <span aria-hidden="true" class="nav-dot"></span>{{ item.title }}
          </RouterLink>
        </div>
      </nav>
      <div class="sidebar-footer"><span class="status-dot" aria-hidden="true"></span>{{ auth.currentWorkspace?.role ?? '尚未加入工作空间' }}</div>
    </aside>
    <div v-if="menuOpen" class="nav-backdrop" aria-hidden="true" @click="menuOpen = false"></div>
    <div class="main-shell">
      <header class="topbar">
        <div class="topbar-start">
          <button v-if="auth.user" ref="menuToggle" class="icon-button menu-toggle" type="button" aria-label="切换导航" aria-controls="sidebar" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen">☰</button>
          <span class="breadcrumb">工作空间 <span aria-hidden="true">/</span> <strong>{{ pageTitle }}</strong></span>
        </div>
        <div class="topbar-actions">
          <template v-if="auth.user"><HeaderSearch /><CreateMenu v-if="auth.workspaceId" :key="auth.workspaceId" /><ImHeaderBadges v-if="auth.canManage && auth.workspaceId" :key="auth.user.id + ':' + auth.workspaceId" /><HeaderNotifications /><button class="theme-button" type="button" aria-controls="app-drawer" :aria-expanded="drawer.open" @click="drawer.toggle()">{{ drawer.open ? '收起 AI 助手' : 'AI 助手' }}</button></template>
          <select v-if="auth.workspaces.length > 1" aria-label="当前工作空间" :value="auth.workspaceId" @change="auth.selectWorkspace(($event.target as HTMLSelectElement).value)"><option v-for="workspace in auth.workspaces" :key="workspace.id" :value="workspace.id">{{ workspace.name }}</option></select>
          <span v-else-if="auth.currentWorkspace" class="user-label">{{ auth.currentWorkspace.name }}</span>
          <span v-if="auth.user" class="user-label">{{ auth.user.display_name }}</span>
          <button class="theme-button" type="button" :aria-label="theme.theme === 'light' ? '切换到暗色主题' : '切换到亮色主题'" @click="theme.toggle">{{ theme.theme === 'light' ? '☾ 暗色' : '☀ 亮色' }}</button>
          <button v-if="auth.user" class="theme-button" :disabled="loggingOut" @click="logout">{{ loggingOut ? '正在登出…' : '登出' }}</button>
        </div>
      </header>
      <main id="main-content" tabindex="-1">
        <p v-if="auth.user && auth.currentWorkspace" class="eyebrow">{{ auth.currentWorkspace.name }}</p>
        <p v-if="logoutError" role="alert" class="feedback">{{ logoutError }}</p>
        <p v-if="auth.loading" role="status">正在连接工作空间…</p>
        <RouterView v-else />
      </main>
      <footer class="page-footer">My Workspace · 自托管个人工作台</footer>
    </div>
    <AppDrawer />
  </div>
</template>
