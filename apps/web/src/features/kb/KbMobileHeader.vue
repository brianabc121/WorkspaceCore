<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { ArrowLeft } from '@lucide/vue'

// 手机顶栏（T-0099）：返回、标题、右侧操作；电脑上隐藏（kb.css）
defineProps<{ title: string; back?: string; backLabel?: string }>()
</script>

<template>
  <header class="kb-mobile-header">
    <RouterLink :to="back ?? '/'" class="kb-icon-btn" :aria-label="backLabel ?? (back ? '返回' : '返回工作台')"><ArrowLeft aria-hidden="true" /></RouterLink>
    <h1>{{ title }}</h1>
    <slot />
  </header>
</template>
