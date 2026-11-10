<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight, Clock, ExternalLink, Info, LayoutGrid, Monitor, Layers, Settings, TriangleAlert, Wrench } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { canSee } from '../../app/navigation'
import type { ModuleView } from './viewModel'

// 功能模块卡片（T-0211，效果图左侧）：名称、版本、说明；启用开关（owner）；部署 / 同步提示；
// 声明的能力数量（不是已授权数量）；外部服务的最近一次检测结果；打开应用、应用设置、查看详情。
const props = defineProps<{ view: ModuleView; selected: boolean; busy: boolean }>()
defineEmits<{ toggle: []; details: [opener: HTMLElement | null] }>()
const auth = useAuthStore()
const item = computed(() => props.view.item)
const visible = (path: string) => {
  const nav = props.view.nav.find(entry => entry.path === path)
  return !nav || canSee(nav, auth)
}
const primary = computed(() => (item.value.module.enabled && props.view.primary && visible(props.view.primary.path) ? props.view.primary : null))
const settings = computed(() => (item.value.module.enabled && props.view.settings && visible(props.view.settings.path) ? props.view.settings : null))
const services = computed(() => item.value.capabilities.services)
</script>

<template>
  <article class="ws-panel apc-card" :class="{ 'is-selected': selected }" :aria-label="`模块：${view.name}`" :data-module="view.key">
    <div class="apc-card-head">
      <span class="apc-app-icon" aria-hidden="true"><component :is="view.icon" /></span>
      <div class="apc-card-title">
        <h3>{{ view.name }} <span class="badge">v{{ view.version }}</span> <span class="apc-tag">功能模块</span><span v-if="view.category" class="apc-tag is-muted">{{ view.category }}</span></h3>
        <p class="apc-card-desc">{{ view.description }}</p>
      </div>
      <div class="apc-card-state">
        <button v-if="item.actions.can_toggle" type="button" role="switch" class="app-switch" :aria-checked="item.module.enabled" :aria-label="`启用${view.name}`" :disabled="busy" @click="$emit('toggle')">
          <span class="app-switch-track" aria-hidden="true"><span class="app-switch-thumb"></span></span>
          <span>{{ busy ? '正在保存…' : view.enabled.text }}</span>
        </button>
        <span v-else class="asv-state" :class="`tone-${view.enabled.tone}`">{{ view.enabled.text }}</span>
        <span v-if="!item.module.ready" class="asv-state tone-amber"><TriangleAlert class="apc-inline-icon" aria-hidden="true" />待部署</span>
        <span v-if="item.sync.state === 'failed' || item.sync.state === 'unknown'" class="asv-state" :class="`tone-${view.sync.tone}`">{{ view.sync.text }}</span>
      </div>
    </div>

    <ul class="apc-caps" aria-label="声明的能力">
      <li><Monitor aria-hidden="true" />{{ view.counts.pages }} 个页面</li>
      <li><LayoutGrid aria-hidden="true" />{{ view.counts.widgets }} 个首页组件</li>
      <li><Wrench aria-hidden="true" />{{ view.counts.tools }} 个{{ view.counts.readOnlyTools ? '只读' : '' }}工具</li>
      <li><Clock aria-hidden="true" />{{ view.counts.crons }} 个定时任务</li>
    </ul>

    <div v-if="services.length" class="apc-service-row">
      <Layers aria-hidden="true" /><span class="apc-service-label">外部服务</span>
      <strong>{{ services.map(service => service.name).join('、') }}</strong>
      <span class="asv-state" :class="`tone-${view.health.tone}`">{{ view.health.text }}</span>
      <small v-if="item.health.stale" class="form-help">最近一次检测的结果，已过期</small>
    </div>
    <p v-if="view.notice" class="apc-notice" :class="{ 'is-danger': item.sync.state === 'failed' }"><Info aria-hidden="true" />{{ view.notice }}</p>
    <p v-if="view.frontendMissing" class="apc-notice is-danger"><TriangleAlert aria-hidden="true" />前后端版本不一致：前端还没有这个模块的页面，入口暂不可用。请把工作台前端与后端更新到同一版本。</p>

    <div class="apc-card-actions">
      <RouterLink v-if="primary" class="secondary-button apc-button" :to="primary.path"><ExternalLink aria-hidden="true" />打开应用</RouterLink>
      <RouterLink v-if="settings" class="secondary-button apc-button" :to="settings.path"><Settings aria-hidden="true" />应用设置</RouterLink>
      <span v-if="!item.module.enabled && (view.primary || view.settings)" class="form-help">启用后可以打开应用与设置。</span>
      <button type="button" class="apc-link-button apc-details" :aria-label="`查看详情：${view.name}`" @click="$emit('details', $event.currentTarget as HTMLElement)">查看详情<ArrowRight aria-hidden="true" /></button>
    </div>
  </article>
</template>
