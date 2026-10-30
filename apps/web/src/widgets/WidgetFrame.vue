<script setup lang="ts">
import { computed } from 'vue'
import { parseWidget, registry } from './registry'
import type { Schema } from '../services/api'
const props = defineProps<{ widget: Schema['WidgetOut']; overview?: Schema['DashboardOverview']; loading?: boolean; failed?: boolean; editing?: boolean }>()
defineEmits<{ retry: []; configure: []; remove: [] }>()
const value = computed(() => parseWidget(props.widget))
const definition = computed(() => value.value ? registry[value.value.widget_type] : null)
</script>
<template>
  <article :id="`widget-${widget.id}`" class="panel widget-card" :aria-label="definition?.title ?? '未知组件'" :data-widget-id="widget.id">
    <header class="widget-header"><h2 :class="{ 'widget-handle': editing }">{{ definition?.title ?? '无法显示组件' }}</h2><div v-if="editing" class="widget-actions"><button v-if="definition" class="icon-button" :aria-label="`配置${definition.title}`" @click="$emit('configure')">设置</button><button class="icon-button" aria-label="删除组件" @click="$emit('remove')">删除</button></div></header>
    <div class="widget-body">
      <component :is="definition.component" v-if="value && definition" :value="value" :overview="overview" :loading="loading" :failed="failed" @retry="$emit('retry')" />
      <p v-else role="alert" class="card-state">此组件类型或配置不受支持，其他组件仍可使用。</p>
      <slot />
    </div>
  </article>
</template>
