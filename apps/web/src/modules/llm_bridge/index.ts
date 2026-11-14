import { Cable } from '@lucide/vue'
import { defineModule } from '../types'

// 大模型客户端管理（docs/v2/LLM_BRIDGE_PLAN.md 第 19 节；模块写法见 docs/modules/README.md）。
// 页面上的“MCP 管理器”就是一座登记的会话桥。
// 后端清单：services/api/app/modules/llm_bridge/manifest.py。页面 owner / admin 能看，只有 owner 能改。
export default defineModule({
  key: 'llm_bridge',
  // 应用中心：“打开应用”
  management: { primaryPath: '/llm_bridge', category: '智能体' },
  nav: [
    { path: '/llm_bridge', title: '大模型客户端', section: '应用', description: '管理一个或多个 MCP 管理器：每个管理器控制选定的平台（ChatGPT / Codex / Claude / WorkBuddy）、单独登录一套账号，智能体经它发指令并管理会话（owner 修改，admin 只读）。每个管理器是独立部署的容器。', minRole: 'admin', icon: Cable },
  ],
  routes: [
    { path: '/llm_bridge', component: () => import('./LlmBridgePage.vue'), meta: { wide: true } },
  ],
})
