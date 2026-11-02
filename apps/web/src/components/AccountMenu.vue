<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronsUpDown, Lock, LogOut, Settings } from '@lucide/vue'
import { useAuthStore } from '../stores/auth'
import { roleNames } from '../app/navIcons'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'

// 侧栏底部的账户区域：显示名与角色；菜单里是个人设置、安全设置和登出（T-0083）
defineProps<{ loggingOut: boolean }>()
const emit = defineEmits<{ logout: [] }>()
const auth = useAuthStore(), router = useRouter()
const name = computed(() => auth.user?.display_name ?? '')
const initial = computed(() => Array.from(name.value.trim())[0]?.toUpperCase() ?? '?')
const role = computed(() => (auth.currentWorkspace ? roleNames[auth.currentWorkspace.role] ?? auth.currentWorkspace.role : '尚未加入工作空间'))
</script>

<template>
  <DropdownMenu v-if="auth.user">
    <DropdownMenuTrigger class="account-trigger" aria-label="账户菜单">
      <span class="avatar" aria-hidden="true">{{ initial }}</span>
      <span class="account-text"><span class="user-label">{{ name }}</span><small>{{ role }}</small></span>
      <ChevronsUpDown class="account-chevron" aria-hidden="true" />
    </DropdownMenuTrigger>
    <DropdownMenuContent side="top" align="start" :collision-padding="8" class="w-60">
      <DropdownMenuLabel class="account-menu-label"><strong>{{ name }}</strong><small>{{ auth.user.email }}</small></DropdownMenuLabel>
      <DropdownMenuSeparator />
      <DropdownMenuItem @select="router.push('/settings')"><Settings aria-hidden="true" />个人设置</DropdownMenuItem>
      <DropdownMenuItem @select="router.push('/settings/security')"><Lock aria-hidden="true" />安全设置</DropdownMenuItem>
      <DropdownMenuSeparator />
      <DropdownMenuItem variant="destructive" :disabled="loggingOut" @select="emit('logout')"><LogOut aria-hidden="true" />{{ loggingOut ? '正在登出…' : '登出' }}</DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>
</template>
