<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { navIcon } from '../app/navIcons'
import { useImBadges } from '../features/im/queries'

// 手机底部导航（T-0086）：最常用的 5 个页面；其余页面在“切换导航”打开的侧栏里。
// 消息只给 owner / admin（与侧栏、路由守卫一致），其他角色第五项是知识库。
const auth = useAuthStore()
const badges = auth.canManage && auth.workspaceId ? useImBadges(auth.workspaceId) : null
const items = computed(() => [
  { path: '/', title: '总览' }, { path: '/tasks', title: '任务' }, { path: '/projects', title: '项目' }, { path: '/calendar', title: '日历' },
  auth.canManage ? { path: '/im', title: '消息' } : { path: '/knowledge', title: '知识库' },
])
const unread = computed(() => badges?.data.value?.im_unread ?? 0)
</script>

<template>
  <nav class="bottom-nav" aria-label="底部导航">
    <RouterLink v-for="item in items" :key="item.path" :to="item.path" class="bottom-nav-link" exact-active-class="is-active" :aria-label="item.path === '/im' && unread ? `消息，${unread} 条未读` : undefined">
      <span class="bottom-nav-icon"><component :is="navIcon(item.path)" aria-hidden="true" /><span v-if="item.path === '/im' && unread" class="count-badge" aria-hidden="true">{{ unread > 99 ? '99+' : unread }}</span></span>
      <span>{{ item.title }}</span>
    </RouterLink>
  </nav>
</template>
