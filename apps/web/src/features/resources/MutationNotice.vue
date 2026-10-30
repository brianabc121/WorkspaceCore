<script setup lang="ts">
defineProps<{ error: string; message: string; needsReload: boolean; busy: boolean; reloadLabel: string }>()
defineEmits<{ reload: [] }>()
</script>
<template><div v-if="error || message" class="feedback"><p v-if="error" role="alert">{{ error }}</p><p v-if="message" role="status">{{ message }}</p><button v-if="needsReload" class="secondary-button" :disabled="busy" @click="$emit('reload')">{{ reloadLabel }}</button></div></template>
