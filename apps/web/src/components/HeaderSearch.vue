<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { Search } from '@lucide/vue'
import { useAuthStore } from '../stores/auth'
import ScopedSearch from '../features/search/ScopedSearch.vue'
// 搜索框挂到 body 下：顶栏是 sticky 的，会形成层叠上下文（T-0083）
const auth = useAuthStore(), open = ref(false)
function show() { if (auth.user && auth.workspaceId) open.value = true }
function shortcut(event: KeyboardEvent) { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); show() } }
watch(() => [auth.user?.id, auth.workspaceId], () => { open.value = false }, { flush: 'sync' })
onMounted(() => window.addEventListener('keydown', shortcut))
onUnmounted(() => window.removeEventListener('keydown', shortcut))
</script>
<template><button class="search-trigger" type="button" aria-keyshortcuts="Control+K Meta+K" @click="show"><Search aria-hidden="true" /><span class="topbar-label search-label">搜索</span> <kbd class="topbar-label">Ctrl K</kbd></button><Teleport to="body"><ScopedSearch v-if="open && auth.user && auth.workspaceId" :key="auth.user.id + ':' + auth.workspaceId" @close="open = false" /></Teleport></template>
