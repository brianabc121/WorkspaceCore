<script setup lang="ts">
import { computed } from 'vue'
import { parseWidget, registry } from './registry'
import type { Schema } from '../services/api'
import { useAuthStore } from '../stores/auth'
import { useModulesStore } from '../stores/modules'
const props = defineProps<{ widget: Schema['WidgetOut']; overview?: Schema['DashboardOverview']; loading?: boolean; failed?: boolean; editing?: boolean }>()
defineEmits<{ retry: []; configure: []; remove: [] }>()
const auth = useAuthStore(), modules = useModulesStore()
const value = computed(() => parseWidget(props.widget))
const definition = computed(() => value.value ? registry[value.value.widget_type] : null)
// 插拔式模块的组件（T-0159）：模块停用时不加载组件，只显示“功能已停用”
const disabled = computed(() => !!definition.value?.module && !modules.isEnabled(definition.value.module))
const moduleName = computed(() => modules.items.find((item) => item.key === definition.value?.module)?.name ?? definition.value?.title)
</script>
<template>
  <article :id="`widget-${widget.id}`" class="panel widget-card" :aria-label="definition?.title ?? '未知组件'" :data-widget-id="widget.id">
    <header class="widget-header"><h2 :class="{ 'widget-handle': editing }">{{ definition?.title ?? '无法显示组件' }}</h2><div v-if="editing" class="widget-actions"><button v-if="definition" class="icon-button" :aria-label="`配置${definition.title}`" @click="$emit('configure')">设置</button><button class="icon-button" aria-label="删除组件" @click="$emit('remove')">删除</button></div></header>
    <div class="widget-body">
      <div v-if="disabled" class="card-state"><p>功能已停用：「{{ moduleName }}」在当前工作空间没有启用。</p><RouterLink v-if="auth.isOwner" class="primary-link" to="/settings/modules">到应用中心启用 →</RouterLink></div>
      <component :is="definition.component" v-else-if="value && definition" :value="value" :overview="overview" :loading="loading" :failed="failed" @retry="$emit('retry')" />
      <p v-else role="alert" class="card-state">此组件类型或配置不受支持，其他组件仍可使用。</p>
      <slot />
    </div>
  </article>
</template>
