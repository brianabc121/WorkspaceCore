<script setup lang="ts">
import { computed } from 'vue'
import { LayoutGrid, Moon, Users, UserPlus } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { useThemeStore } from '../../stores/theme'
import { roleNames } from '../../app/navIcons'
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet'
import ChatAvatar from './ChatAvatar.vue'

// 消息页左上角的侧滑菜单（T-0095）：独立页面没有工作台的侧栏，从这里返回工作台、打开通讯录、新建群聊、切换夜间模式。
const open = defineModel<boolean>('open', { required: true })
const emit = defineEmits<{ contacts: []; create: [] }>()
const auth = useAuthStore(), theme = useThemeStore()
const name = computed(() => auth.user?.display_name ?? '我')
const where = computed(() => [auth.currentWorkspace?.name, roleNames[auth.currentWorkspace?.role ?? '']].filter(Boolean).join(' · '))
function choose(action: 'contacts' | 'create') { open.value = false; if (action === 'contacts') emit('contacts'); else emit('create') }
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent side="left" class="chat-drawer gap-0 p-0" :aria-describedby="undefined">
      <SheetTitle class="sr-only">菜单</SheetTitle>
      <div class="chat-drawer-head">
        <ChatAvatar :name="name" self size="md" />
        <span class="chat-drawer-who"><strong>{{ name }}</strong><span>{{ where }}</span></span>
      </div>
      <nav class="chat-drawer-nav" aria-label="消息页菜单">
        <RouterLink to="/" class="chat-drawer-item"><LayoutGrid aria-hidden="true" />返回工作台</RouterLink>
        <button type="button" class="chat-drawer-item" @click="choose('contacts')"><Users aria-hidden="true" />通讯录</button>
        <button type="button" class="chat-drawer-item" @click="choose('create')"><UserPlus aria-hidden="true" />新建群聊</button>
        <button type="button" class="chat-drawer-item" role="switch" :aria-checked="theme.theme === 'dark'" @click="theme.toggle">
          <Moon aria-hidden="true" />夜间模式<span class="chat-switch" aria-hidden="true"><span></span></span>
        </button>
      </nav>
    </SheetContent>
  </Sheet>
</template>
