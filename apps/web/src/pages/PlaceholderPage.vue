<script setup lang="ts">
import { useRoute } from 'vue-router'
const route = useRoute()
</script>
<template>
  <div class="page-heading"><h1>{{ route.meta.title }}</h1><p>{{ route.meta.description }}</p></div>
  <section class="panel empty-state" aria-label="功能开发状态">
    <span class="badge">规划中</span><h2>这个空间正在准备中</h2>
    <p>当前仅提供导航与页面骨架，尚未启用业务功能或连接外部服务。</p>
    <RouterLink class="primary-link" to="/">返回总览</RouterLink>
  </section>
</template>
