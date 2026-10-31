<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useAuthStore } from '../stores/auth'
import ScopedSearch from '../features/search/ScopedSearch.vue'
const auth = useAuthStore(), open = ref(false)
function show() { if (auth.user && auth.workspaceId) open.value = true }
function shortcut(event: KeyboardEvent) { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); show() } }
watch(() => [auth.user?.id, auth.workspaceId], () => { open.value = false }, { flush: 'sync' })
onMounted(() => window.addEventListener('keydown', shortcut))
onUnmounted(() => window.removeEventListener('keydown', shortcut))
</script>
<template><button class="theme-button" aria-keyshortcuts="Control+K Meta+K" @click="show">搜索 <kbd>⌘ / Ctrl K</kbd></button><ScopedSearch v-if="open && auth.user && auth.workspaceId" :key="auth.user.id + ':' + auth.workspaceId" @close="open = false" /></template>
