<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { navigation } from './app/navigation'
import { useThemeStore } from './stores/theme'

const route = useRoute()
const theme = useThemeStore()
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
    <aside id="sidebar" class="sidebar" :class="{ 'is-open': menuOpen }">
      <RouterLink class="brand" to="/" aria-label="My Workspace 首页">
        <span class="brand-mark" aria-hidden="true">W</span>
        <span>My Workspace<small>你的个人工作空间</small></span>
      </RouterLink>
      <nav aria-label="主导航">
        <div v-for="section in sections" :key="section" class="nav-section">
          <p class="nav-label">{{ section }}</p>
          <RouterLink v-for="item in navigation.filter((entry) => entry.section === section)" :key="item.path" :to="item.path" class="nav-link" exact-active-class="is-active">
            <span aria-hidden="true" class="nav-dot"></span>{{ item.title }}
          </RouterLink>
        </div>
      </nav>
      <div class="sidebar-footer"><span class="status-dot" aria-hidden="true"></span>开发预览 · 尚未连接服务</div>
    </aside>
    <div v-if="menuOpen" class="nav-backdrop" aria-hidden="true" @click="menuOpen = false"></div>
    <div class="main-shell">
      <header class="topbar">
        <div class="topbar-start">
          <button ref="menuToggle" class="icon-button menu-toggle" type="button" aria-label="切换导航" aria-controls="sidebar" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen">☰</button>
          <span class="breadcrumb">工作空间 <span aria-hidden="true">/</span> <strong>{{ pageTitle }}</strong></span>
        </div>
        <button class="theme-button" type="button" :aria-label="theme.theme === 'light' ? '切换到暗色主题' : '切换到亮色主题'" @click="theme.toggle">{{ theme.theme === 'light' ? '☾ 暗色' : '☀ 亮色' }}</button>
      </header>
      <main id="main-content" tabindex="-1">
        <p class="eyebrow">Sprint 0 · 工程骨架</p>
        <RouterView />
      </main>
      <footer class="page-footer">My Workspace · 自托管个人工作台</footer>
    </div>
  </div>
</template>
