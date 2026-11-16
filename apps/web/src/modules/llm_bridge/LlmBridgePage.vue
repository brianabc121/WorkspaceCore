<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { Box } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import ManagementPage from '../../components/management/ManagementPage.vue'
import LlmBridgeWorkspace from './LlmBridgeWorkspace.vue'
import RecordsWorkspace from './RecordsWorkspace.vue'
import DigestsWorkspace from './DigestsWorkspace.vue'
import LlmChatWorkspace from './chat/LlmChatWorkspace.vue'
import './chat/llm_chat.css'

// 大模型客户端：一个入口、四个视图。
// - 对话（默认，/llm_bridge）：像各家的客户端一样看会话、提问（docs/v2/LLM_CHAT_PLAN.md）。
// - 管理器与账号（/llm_bridge?view=manage）：添加 MCP 管理器、登录账号、设置、实例权限（LLM_BRIDGE_PLAN.md 第 19 节）。
//   从服务器页面跳过来时带 &m=<管理器>（选中它）或 &deploy=<类型>:<服务器>（打开一键部署并选好那台服务器，LLM_BRIDGE_DEPLOY_PLAN.md 第 7 节）。
// - 会话记录（/llm_bridge?view=records）：跨管理器查找、回溯会话，看用量（docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 4.2）。
// - 会话纪要（/llm_bridge?view=digests）：工作台整理进知识库的会话纪要（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2）。
// owner / admin 能看（管理视图里 admin 只读），其他角色路由已拦截。
// 换用户或工作空间时整页重建，表单、登录进度、草稿不会串到另一个工作空间。
const auth = useAuthStore(), route = useRoute()
const manage = computed(() => route.query.view === 'manage')
const records = computed(() => route.query.view === 'records')
const digests = computed(() => route.query.view === 'digests')
const chat = computed(() => !manage.value && !records.value && !digests.value)
// 页头的一句说明随视图变化
const subtitle = computed(() => (manage.value ? '管理运行位置、平台登录与智能体访问' : records.value ? '跨管理器查找、回溯会话，看用量' : digests.value ? '回顾会话记录，将值得保留的内容转成正式笔记' : '连接你的 AI，让对话与工作衔接'))
const scopeKey = computed(() => `${auth.user?.id}:${auth.workspaceId}`)
</script>

<template>
  <template v-if="auth.canManage && auth.workspaceId">
    <header class="lc-views">
      <div class="lc-hero">
        <span class="lc-hero-icon" aria-hidden="true"><Box /></span>
        <div class="lc-hero-text">
          <h1>大模型客户端</h1>
          <p>{{ subtitle }}</p>
        </div>
      </div>
      <nav aria-label="大模型客户端的视图">
        <RouterLink :to="{ path: '/llm_bridge' }" :aria-current="chat ? 'page' : undefined">对话</RouterLink>
        <RouterLink :to="{ path: '/llm_bridge', query: { view: 'records' } }" :aria-current="records ? 'page' : undefined">会话记录</RouterLink>
        <RouterLink :to="{ path: '/llm_bridge', query: { view: 'digests' } }" :aria-current="digests ? 'page' : undefined">会话纪要</RouterLink>
        <RouterLink :to="{ path: '/llm_bridge', query: { view: 'manage' } }" :aria-current="manage ? 'page' : undefined">管理器与账号</RouterLink>
      </nav>
    </header>
    <LlmBridgeWorkspace v-if="manage" :key="`m:${scopeKey}`" :scope="auth.workspaceId" />
    <RecordsWorkspace v-else-if="records" :key="`r:${scopeKey}`" :scope="auth.workspaceId" />
    <DigestsWorkspace v-else-if="digests" :key="`d:${scopeKey}`" :scope="auth.workspaceId" />
    <LlmChatWorkspace v-else :key="`c:${scopeKey}`" :scope="auth.workspaceId" />
  </template>
  <ManagementPage v-else title="大模型客户端管理"><p role="alert">只有 owner / admin 能查看大模型客户端。</p></ManagementPage>
</template>
