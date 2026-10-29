import { createRouter, createWebHistory, type RouterHistory } from 'vue-router'
import { navigation } from './navigation'
import HomePage from '../pages/HomePage.vue'
import PlaceholderPage from '../pages/PlaceholderPage.vue'
import NotFoundPage from '../pages/NotFoundPage.vue'

export function createAppRouter(history: RouterHistory = createWebHistory()) {
  return createRouter({
    history,
    routes: [
      ...navigation.map((item) => ({
        path: item.path,
        component: item.path === '/' ? HomePage : PlaceholderPage,
        meta: { title: item.title, description: item.description },
      })),
      { path: '/:pathMatch(.*)*', component: NotFoundPage, meta: { title: '页面未找到' } },
    ],
    scrollBehavior: () => ({ top: 0 }),
  })
}
