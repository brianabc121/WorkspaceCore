<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useModulesStore } from '../stores/modules'

// 打开停用的插拔式模块的页面时原地显示（T-0159）：地址不变，owner 在应用中心启用后页面直接恢复
const props = defineProps<{ moduleKey: string }>()
const auth = useAuthStore(), modules = useModulesStore()
const name = computed(() => modules.items.find((item) => item.key === props.moduleKey)?.name ?? '这个功能')
</script>

<template>
  <section class="panel empty-state">
    <span class="badge">未启用</span>
    <h1>功能未启用</h1>
    <p>「{{ name }}」在当前工作空间没有启用。</p>
    <template v-if="auth.isOwner">
      <p>可以在应用中心启用它；停用期间数据都保留，启用后就恢复。</p>
      <RouterLink class="primary-link" to="/settings/modules">前往应用中心</RouterLink>
    </template>
    <template v-else>
      <p>需要工作空间的 owner 在“设置 → 应用中心”里启用。</p>
      <RouterLink class="primary-link" to="/">返回总览</RouterLink>
    </template>
  </section>
</template>
