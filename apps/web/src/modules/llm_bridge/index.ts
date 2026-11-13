import { defineModule } from '../types'

// 大模型会话桥（模块写法见 docs/modules/README.md）。页面按需加载，模块停用时侧栏不显示
export default defineModule({
  key: 'llm_bridge',
  nav: [
    { path: '/llm-bridge', title: '大模型会话桥', section: '应用', description: '让智能体经会话桥向 ChatGPT / Codex / Claude / WorkBuddy 发指令并管理会话。依赖独立部署的会话桥。' },
  ],
  routes: [
    { path: '/llm-bridge', component: () => import('./LlmBridgePage.vue'), meta: { title: '大模型会话桥' } },
  ],
})
