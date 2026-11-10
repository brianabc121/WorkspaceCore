<script setup lang="ts">
import { Activity, Cloud, Copy, Ellipsis, KeyRound, Layers, Pencil, Power, Trash2 } from '@lucide/vue'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { TYPE_LABEL, checkStatus, configStatus, isStale, type Connection } from './api'
import ModelCell from './ModelCell.vue'

// 连接表（效果图主表）：连接（名称、类型、URL）、支持模型、状态（配置 / 启停与最近检测分开）、关联智能体数、管理与“…”菜单。
// 点一行或“管理”打开右侧详情；菜单：编辑、检测、停用 / 启用、复制为新连接、删除。
defineProps<{ items: Connection[]; selected: string | null; freshSeconds: number }>()
const emit = defineEmits<{ select: [id: string, row: HTMLElement | null]; check: [c: Connection, row: HTMLElement | null]; toggle: [c: Connection]; copy: [c: Connection]; remove: [c: Connection] }>()
const row = (event: Event) => (event.currentTarget as HTMLElement | null)?.closest('tr') ?? null
let opener: HTMLElement | null = null
const remember = (event: Event) => { opener = row(event) }
const typeLabel = (c: Connection) => (c.connection_type ? TYPE_LABEL[c.connection_type] : '未选类型')
const showCheck = (c: Connection) => c.configuration_state === 'ready' || !!c.last_check
// 菜单项选中后菜单要先关上并把焦点还给“…”按钮，再打开抽屉或对话框（之后焦点能回到这个按钮）
const later = (fn: () => void) => { setTimeout(fn, 0) }
</script>

<template>
  <div class="ws-table-wrap">
    <table class="ws-table asv-table mc-table">
      <thead><tr><th scope="col">连接</th><th scope="col">支持模型</th><th scope="col">状态</th><th scope="col">智能体</th><th scope="col">操作</th></tr></thead>
      <tbody>
        <tr v-for="c in items" :key="c.id" :data-connection="c.id" :aria-selected="selected === c.id" @click="emit('select', c.id, row($event))">
          <th scope="row" data-label="连接">
            <span class="mc-name">
              <span class="mc-icon" aria-hidden="true"><Cloud v-if="c.connection_type === 'official'" /><Layers v-else-if="c.connection_type === 'relay'" /><KeyRound v-else /></span>
              <span class="mc-name-text">
                <span class="mc-name-line"><strong>{{ c.name }}</strong><span class="mc-type" :class="`is-${c.connection_type ?? 'draft'}`">{{ typeLabel(c) }}</span></span>
                <span class="asv-sub mc-url" :title="c.base_url ?? undefined">{{ c.base_url ?? '待补充连接 URL' }}</span>
              </span>
            </span>
          </th>
          <td data-label="支持模型"><ModelCell :connection="c" /></td>
          <td data-label="状态">
            <span class="mc-states">
              <span v-if="configStatus(c).tone !== 'green'" class="asv-state" :class="`tone-${configStatus(c).tone}`">{{ configStatus(c).label }}</span>
              <span v-if="showCheck(c)" class="asv-state" :class="`tone-${checkStatus(c.last_check).tone}`">{{ checkStatus(c.last_check).label }}</span>
            </span>
            <span v-if="c.last_check && isStale(c.last_check, freshSeconds)" class="asv-sub">结果可能已过期</span>
          </td>
          <td data-label="智能体" class="asv-nowrap">{{ c.grant_count }} 个</td>
          <td class="asv-link-cell mc-actions" @click.stop>
            <button type="button" class="asv-link" :aria-label="`管理：${c.name}`" @click="emit('select', c.id, row($event))">管理</button>
            <DropdownMenu>
              <DropdownMenuTrigger class="icon-button mc-more" :aria-label="`更多操作：${c.name}`" @pointerdown="remember" @keydown="remember"><Ellipsis aria-hidden="true" /></DropdownMenuTrigger>
              <DropdownMenuContent align="end" :collision-padding="8">
                <DropdownMenuItem @select="later(() => emit('select', c.id, opener))"><Pencil aria-hidden="true" />编辑连接</DropdownMenuItem>
                <DropdownMenuItem :disabled="!c.protocol || !c.base_url" @select="later(() => emit('check', c, opener))"><Activity aria-hidden="true" />检测连接</DropdownMenuItem>
                <DropdownMenuItem @select="later(() => emit('toggle', c))"><Power aria-hidden="true" />{{ c.enabled ? '停用连接' : '启用连接' }}</DropdownMenuItem>
                <DropdownMenuItem @select="later(() => emit('copy', c))"><Copy aria-hidden="true" />复制为新连接</DropdownMenuItem>
                <DropdownMenuItem variant="destructive" @select="later(() => emit('remove', c))"><Trash2 aria-hidden="true" />删除连接</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
