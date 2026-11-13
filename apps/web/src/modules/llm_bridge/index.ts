import { Cable } from '@lucide/vue'
import { defineModule } from '../types'

// 大模型会话桥（docs/v2/LLM_BRIDGE_PLAN.md；模块写法见 docs/modules/README.md）。
// 后端清单：services/api/app/modules/llm_bridge/manifest.py。页面 owner / admin 能看，只有 owner 能改。
export default defineModule({
  key: 'llm_bridge',
  // 应用中心：“打开应用”
  management: { primaryPath: '/llm_bridge', category: '智能体' },
  nav: [
    { path: '/llm_bridge', title: '大模型会话桥', section: '应用', description: '让智能体经会话桥向 ChatGPT / Codex / Claude / WorkBuddy 发指令并管理会话（owner 修改，admin 只读）。依赖独立部署的会话桥。', minRole: 'admin', icon: Cable },
  ],
  routes: [
    { path: '/llm_bridge', component: () => import('./LlmBridgePage.vue'), meta: { wide: true } },
  ],
})
