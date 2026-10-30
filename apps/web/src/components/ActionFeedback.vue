<script setup lang="ts">
defineProps<{ error: string; notice: string }>()
</script>
<template>
  <p v-if="error" class="feedback" role="alert">{{ error }}</p>
  <p v-if="notice" class="feedback toast" role="status">{{ notice }}</p>
</template>
