<script setup lang="ts">
import { computed, ref, toRef, watch } from 'vue'
import { ChevronDown, ChevronRight, Folder, FolderOpen } from '@lucide/vue'
import type { LlmBridgeApi } from './api'
import { join } from './workFiles'

// 左栏“文件夹”树的一个节点（T-0289）：只列文件夹；展开时才读它下面有什么。
// 当前打开的文件夹（current）所在的那一串会自动展开并标出来。点名字就打开那个文件夹。
const props = defineProps<{ api: LlmBridgeApi; bridgeId: string; session: string; path: string; name: string; current: string; depth?: number }>()
const emit = defineEmits<{ open: [path: string] }>()

const onTrail = computed(() => props.current === props.path || props.current.startsWith(props.path ? `${props.path}/` : ''))
const expanded = ref(onTrail.value)
watch(onTrail, on => { if (on) expanded.value = true })
const listing = props.api.useWorkFiles(toRef(() => props.bridgeId), toRef(() => props.session), toRef(() => props.path), expanded)
const folders = computed(() => (listing.data.value?.entries ?? []).filter(e => e.kind === 'dir'))
const active = computed(() => props.current === props.path)
</script>

<template>
  <li class="lb-tree-node" role="treeitem" :aria-expanded="expanded" :aria-selected="active">
    <div class="lb-tree-row" :class="{ 'is-active': active }" :style="{ paddingLeft: `${(depth ?? 0) * 16 + 4}px` }">
      <button type="button" class="lb-tree-toggle" :aria-label="`${expanded ? '收起' : '展开'}${name}`" @click="expanded = !expanded"><component :is="expanded ? ChevronDown : ChevronRight" aria-hidden="true" /></button>
      <button type="button" class="lb-tree-name" :title="path || name" @click="emit('open', path)"><component :is="expanded ? FolderOpen : Folder" aria-hidden="true" />{{ name }}</button>
    </div>
    <ul v-if="expanded && folders.length" role="group">
      <WorkTreeNode v-for="f in folders" :key="f.name" :api="api" :bridge-id="bridgeId" :session="session" :path="join(path, f.name)" :name="f.name" :current="current" :depth="(depth ?? 0) + 1" @open="emit('open', $event)" />
    </ul>
  </li>
</template>
