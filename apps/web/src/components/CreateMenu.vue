<script setup lang="ts">
import { computed, ref, type ComponentPublicInstance } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { createGroups } from '../app/create'
import { CREATE_QUERY } from '../composables/useCreateIntent'
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'

// 顶栏的全局“创建”菜单（规范 §8.1）。reka-ui 在菜单关闭后用 setTimeout 把焦点还给触发按钮，
// 会抢走表单里刚聚焦的输入框；所以选中一项时由这里接管：先把焦点放回“创建”按钮，再跳转打开表单，
// 表单关闭后焦点也会回到这个按钮。
const auth = useAuthStore(), router = useRouter()
const groups = computed(() => createGroups(auth.currentWorkspace?.role))
const trigger = ref<ComponentPublicInstance | null>(null)
let pending: string | null = null
function afterClose(event: Event) {
  if (!pending) return
  const path = pending
  pending = null
  event.preventDefault()
  setTimeout(() => {
    const button: HTMLElement | undefined = trigger.value?.$el
    button?.focus()
    void router.push({ path, query: { [CREATE_QUERY]: '1' } })
  }, 0)
}
</script>

<template>
  <DropdownMenu v-if="groups.length">
    <DropdownMenuTrigger ref="trigger" class="theme-button"><span aria-hidden="true">＋ </span>创建</DropdownMenuTrigger>
    <DropdownMenuContent align="end" :collision-padding="8" class="w-60" @close-auto-focus="afterClose">
      <template v-for="(group, index) in groups" :key="group.label">
        <DropdownMenuSeparator v-if="index" />
        <DropdownMenuLabel class="text-xs text-muted-foreground">{{ group.label }}</DropdownMenuLabel>
        <DropdownMenuGroup>
          <DropdownMenuItem v-for="item in group.items" :key="item.path" @select="pending = item.path">
            {{ item.label }}<span class="ml-auto text-xs text-muted-foreground">{{ item.hint }}</span>
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </template>
    </DropdownMenuContent>
  </DropdownMenu>
</template>
