<script setup lang="ts">
defineProps<{ title: string; description: string }>()
</script>
<template>
  <section class="panel placeholder-card">
    <span class="badge">规划中</span>
    <h2>{{ title }}</h2>
    <p>{{ description }}</p>
    <span class="card-footnote">尚未启用</span>
  </section>
</template>
