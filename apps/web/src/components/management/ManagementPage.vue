<script setup lang="ts">
import '../../features/workspace/workspace-pages.css'
import '../../features/agents/agents-pages.css'
import './management.css'

// 管理区共用的页面外壳（T-0189，方案 docs/v2/ai-admin-redesign 计划 B1）：标题、一句说明、上下文标签、右上角操作、
// 正文与可选的右侧一栏。只管布局：不发请求、不持有表单与业务状态。样式沿用 workspace-pages / agents-pages。
// embedded：嵌在另一个管理页里（例如中转站管理的“兼容凭据”），标题降为 h2、外层是带名称的 section。
defineProps<{ title: string; description?: string; asideLabel?: string; embedded?: boolean }>()
</script>

<template>
  <component :is="embedded ? 'section' : 'div'" class="mg-page" :class="{ 'is-embedded': embedded }" :aria-label="embedded ? title : undefined">
    <header class="ws-header mg-header">
      <div class="mg-title">
        <component :is="embedded ? 'h2' : 'h1'">{{ title }}</component>
        <p v-if="description">{{ description }}</p>
        <div v-if="$slots.context" class="mg-context"><slot name="context" /></div>
      </div>
      <div v-if="$slots.actions" class="ws-actions"><slot name="actions" /></div>
    </header>
    <div class="mg-body" :class="{ 'has-aside': !!$slots.aside }">
      <div class="mg-main"><slot /></div>
      <aside v-if="$slots.aside" class="mg-aside" :aria-label="asideLabel ?? `${title}：侧栏`"><slot name="aside" /></aside>
    </div>
  </component>
</template>
