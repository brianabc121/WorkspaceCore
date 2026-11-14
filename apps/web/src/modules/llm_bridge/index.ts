import { defineAsyncComponent, defineComponent, h } from 'vue'
import { Cable } from '@lucide/vue'
import { defineModule } from '../types'
import type { TargetKind } from './api'

// 大模型客户端：聊天（docs/v2/LLM_CHAT_PLAN.md）和管理器与账号（docs/v2/LLM_BRIDGE_PLAN.md 第 19 节）；模块写法见 docs/modules/README.md。
// 页面上的“MCP 管理器”就是一座登记的会话桥。
// 后端清单：services/api/app/modules/llm_bridge/manifest.py。页面 owner / admin 能用；管理器与账号只有 owner 能改。

// 两个服务器页面上的“MCP 管理器”区块（docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 7 节）：同一个组件，按位置告诉它是哪一类服务器；按需加载
const ServerManagersPanel = defineAsyncComponent(() => import('./ServerManagersPanel.vue'))
const serverPanel = (kind: TargetKind) => defineComponent({
  name: 'LlmBridgeServerPanel',
  props: { serverId: { type: String, required: true }, serverName: { type: String, required: true } },
  setup: props => () => h(ServerManagersPanel, { kind, serverId: props.serverId, serverName: props.serverName }),
})

export default defineModule({
  key: 'llm_bridge',
  // 应用中心：“打开应用”
  management: { primaryPath: '/llm_bridge', category: '智能体' },
  nav: [
    { path: '/llm_bridge', title: '大模型客户端', section: '应用', description: '像各家的客户端一样和 ChatGPT / Codex / Claude / WorkBuddy 对话、看智能体开的会话；并管理一个或多个 MCP 管理器：每个管理器控制选定的平台、单独登录一套账号，智能体经它发指令并管理会话（owner / admin 可用，管理器与账号只有 owner 能改）。每个管理器是独立部署的容器。', minRole: 'admin', icon: Cable },
  ],
  routes: [
    { path: '/llm_bridge', component: () => import('./LlmBridgePage.vue'), meta: { wide: true } },
  ],
  // 智能体服务器详情、容器服务器“服务器设置”里的“MCP 管理器”区块
  serverPanels: { agentServer: serverPanel('agent_server'), runnerHost: serverPanel('runner_host') },
})
