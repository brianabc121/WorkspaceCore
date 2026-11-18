<script setup lang="ts">
import { ref } from 'vue'
import { Activity, ArrowRight, Box, Clock, Play } from '@lucide/vue'
import { platformName, type BridgeStatus } from './api'

// 运行概览（T-0289，效果图“管理器与账号”）：放在详情的标签上面，几个标签共用。
// 进行中、排队中的回合数，管理器的版本；展开看每个平台同时在跑几个、上限多少。
// 数的是回合：一个会话同时只跑一个回合，排队的可以有好几个。
defineProps<{ status: BridgeStatus }>()
const showLoad = ref(false)
</script>

<template>
  <div class="lb-overview-card" role="group" aria-label="运行概览">
    <div class="lb-overview-lead">
      <span class="lb-overview-icon" aria-hidden="true"><Activity /></span>
      <div>
        <strong>运行概览</strong><span>当前 MCP 管理器</span>
        <button v-if="Object.keys(status.load).length" type="button" class="asv-link lb-overview-more" :aria-expanded="showLoad" @click="showLoad = !showLoad">{{ showLoad ? '收起平台并发' : '查看平台并发' }}<ArrowRight aria-hidden="true" /></button>
      </div>
    </div>
    <div class="lb-overview-stat"><Play aria-hidden="true" /><span>进行中</span><b>{{ status.running }}<small> 个回合</small></b></div>
    <div class="lb-overview-stat"><Clock aria-hidden="true" /><span>排队中</span><b>{{ status.queued }}<small> 个回合</small></b></div>
    <div v-if="status.version" class="lb-overview-stat"><Box aria-hidden="true" /><span>版本</span><b class="lb-overview-version">{{ status.version }}</b></div>
  </div>
  <ul v-if="showLoad" class="lb-load" aria-label="各平台的并发">
    <li v-for="(load, platform) in status.load" :key="platform" :class="{ 'is-full': load.running >= load.limit }">{{ platformName(String(platform)) }}：{{ load.running }} / {{ load.limit }} 个会话在跑<template v-if="load.waiting">，{{ load.waiting }} 个在等</template></li>
  </ul>
</template>
