<script setup lang="ts">
// 管理区的一块面板（T-0189）：标题、说明、右侧操作与正文。只管布局；状态反馈沿用 DataState / ActionFeedback。
defineProps<{ title: string; description?: string; label?: string }>()
</script>

<template>
  <section class="ws-panel mg-section" :aria-label="label ?? title">
    <div class="mg-section-head">
      <div class="mg-section-title">
        <h2><slot name="title">{{ title }}</slot></h2>
        <p v-if="description" class="form-help">{{ description }}</p>
      </div>
      <div v-if="$slots.actions" class="mg-section-actions"><slot name="actions" /></div>
    </div>
    <slot />
  </section>
</template>
