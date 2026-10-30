<script setup lang="ts">
import { safeUrl } from '../widgets/registry'
defineProps<{ href: string; label?: string }>()
</script>
<template><a v-if="safeUrl(href)" class="external-link" :href="href" target="_blank" rel="noopener noreferrer">{{ label ?? '在 Vikunja 中打开' }} ↗</a><span v-else>原生链接不可用</span></template>
