<script setup lang="ts">
import { computed, provide, ref } from 'vue'
import { parseWidget, registry } from './registry'
import type { Schema } from '../services/api'
import { useAuthStore } from '../stores/auth'
import { useModulesStore } from '../stores/modules'
import { widgetHeaderKey, type WidgetHeader } from './home/header'
import { CircleAlert, Sparkles } from '@lucide/vue'
const props = defineProps<{ widget: Schema['WidgetOut']; overview?: Schema['DashboardOverview']; loading?: boolean; failed?: boolean; editing?: boolean }>()
defineEmits<{ retry: []; configure: []; remove: [] }>()
const auth = useAuthStore(), modules = useModulesStore()
const value = computed(() => parseWidget(props.widget))
const definition = computed(() => value.value ? registry[value.value.widget_type] : null)
// 插拔式模块的组件（T-0159）：模块停用时不加载组件，只显示“功能已停用”
const disabled = computed(() => !!definition.value?.module && !modules.isEnabled(definition.value.module))
const moduleName = computed(() => modules.items.find((item) => item.key === definition.value?.module)?.name ?? definition.value?.title)
// 标题栏右侧的标记由组件自己设置（T-0164）；bare 的组件不编辑时不显示外框和标题（标题仍给读屏）
const header = ref<WidgetHeader>({})
provide(widgetHeaderKey, header)
const bare = computed(() => !!definition.value?.bare && !props.editing && !disabled.value)
</script>
<template>
  <article :id="`widget-${widget.id}`" class="widget-card" :class="bare ? 'is-bare' : 'panel'" :aria-label="definition?.title ?? '未知组件'" :data-widget-id="widget.id" :data-widget-type="widget.widget_type">
    <header class="widget-header" :class="{ 'sr-only': bare }">
      <div class="widget-title">
        <component :is="definition.icon" v-if="definition?.icon" class="widget-title-icon" aria-hidden="true" />
        <h2 :class="{ 'widget-handle': editing }">{{ definition?.title ?? '无法显示组件' }}</h2>
        <span v-if="header.badge && !editing" class="widget-badge" :class="`widget-badge-${header.badge.tone}`"><CircleAlert v-if="header.badge.tone === 'warning'" aria-hidden="true" /><Sparkles v-else aria-hidden="true" />{{ header.badge.text }}</span>
      </div>
      <span v-if="header.meta && !editing" class="widget-meta">{{ header.meta }}</span>
      <div v-if="editing" class="widget-actions"><button v-if="definition" class="icon-button" :aria-label="`配置${definition.title}`" @click="$emit('configure')">设置</button><button class="icon-button" aria-label="删除组件" @click="$emit('remove')">删除</button></div>
    </header>
    <div class="widget-body">
      <div v-if="disabled" class="card-state"><p>功能已停用：「{{ moduleName }}」在当前工作空间没有启用。</p><RouterLink v-if="auth.isOwner" class="primary-link" to="/settings/modules">到应用中心启用 →</RouterLink></div>
      <component :is="definition.component" v-else-if="value && definition" :value="value" :overview="overview" :loading="loading" :failed="failed" @retry="$emit('retry')" />
      <p v-else role="alert" class="card-state">此组件类型或配置不受支持，其他组件仍可使用。</p>
      <slot />
    </div>
  </article>
</template>
