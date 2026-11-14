<script setup lang="ts">
import { computed } from 'vue'
import { useModulesStore } from '../stores/modules'
import { moduleServerPanels } from './registry'
import type { ServerPanelPlace } from './types'

// 服务器页面上由插拔式模块提供的区块（模块定义里的 serverPanels，例如大模型客户端的“MCP 管理器”）：
// 智能体服务器的详情（agentServer）和容器服务器“服务器设置”里的每台服务器（runnerHost）各放一个本组件，
// 页面本身不导入任何模块的代码。只渲染当前工作空间已启用的模块的区块；一个都没有时什么都不渲染。
// 启用状态还没读到时先不渲染：免得停用的模块的区块闪一下、还去请求它的接口。
const props = defineProps<{ place: ServerPanelPlace; serverId: string; serverName: string }>()
const store = useModulesStore()
const panels = computed(() => (store.loaded ? moduleServerPanels[props.place].filter(panel => store.isEnabled(panel.module)) : []))
</script>

<template>
  <component :is="panel.component" v-for="panel in panels" :key="panel.module" :server-id="serverId" :server-name="serverName" />
</template>
