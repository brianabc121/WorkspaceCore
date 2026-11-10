<script setup lang="ts">
import { computed } from 'vue'
import { AppWindow, ArrowRight, ExternalLink, FileText, LayoutGrid, Link, NotebookText, Pencil, Plus, Trash2 } from '@lucide/vue'
import type { Component } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { errorMessage } from '../../services/api'
import { KIND_LABELS, allSections, hasRole, pageTarget, type CustomPage, type NavigationLayout } from '../../app/sidebar'

// 自定义页面（T-0211，方案第 3.3 节）：侧栏里的外部链接、站内页面、知识库笔记、仪表盘和嵌入页。
// 与“侧栏与页面”共用同一份布局（GET / PUT /api/v1/navigation）；这里每次只改一项并立即整份保存（PageEntryEditor）。
// compact：功能模块页签下的快捷预览（前 3 项）。只有 owner 能改；admin 只读。
const props = defineProps<{ layout: NavigationLayout | null; loaded: boolean; error: unknown; canEdit: boolean; compact?: boolean }>()
defineEmits<{ add: []; edit: [page: CustomPage]; remove: [page: CustomPage]; all: [] }>()
const auth = useAuthStore()
const ICONS: Record<CustomPage['kind'], Component> = { link: Link, page: FileText, note: NotebookText, dashboard: LayoutGrid, embed: AppWindow }
const ROLES: Record<CustomPage['min_role'], string> = { viewer: '所有成员', member: '成员及以上', admin: 'owner 与 admin', owner: '只有 owner' }
const role = computed(() => auth.currentWorkspace?.role)
const pages = computed(() => (props.layout?.pages ?? []).filter(page => hasRole(role.value, page.min_role)))
const shown = computed(() => (props.compact ? pages.value.slice(0, 3) : pages.value))
const sections = computed(() => new Map(allSections(props.layout).map(section => [section.key, section.title])))
</script>

<template>
  <section class="ws-panel apc-pages" :aria-label="compact ? '页面入口' : '自定义页面'">
    <div class="mg-section-head">
      <div class="mg-section-title">
        <h2>{{ compact ? '页面入口' : '自定义页面' }}</h2>
        <p class="form-help">链接、站内页面、笔记、看板和嵌入页，也能在这里集中管理。“谁能看到”只决定入口是否显示，打开后仍按原来的权限检查；移除入口不会删除它指向的内容。</p>
      </div>
      <div v-if="canEdit && !compact" class="mg-section-actions"><button type="button" class="secondary-button" :disabled="pages.length >= 50" @click="$emit('add')"><Plus aria-hidden="true" />添加入口</button></div>
    </div>
    <p v-if="!loaded && !layout" role="status" class="ws-state-line">正在加载页面入口…</p>
    <p v-else-if="!layout" role="alert" class="ws-state-line">页面入口读取失败：{{ errorMessage(error) }}</p>
    <p v-else-if="!pages.length" class="ws-state-line">还没有自定义页面。<template v-if="canEdit">点“添加入口”放一个常用链接、笔记或看板到侧栏。</template></p>
    <ul v-else class="apc-page-list">
      <li v-for="page in shown" :key="page.id" :aria-label="`入口：${page.title}`">
        <component :is="ICONS[page.kind]" class="apc-page-icon" aria-hidden="true" />
        <div class="apc-page-name">
          <strong>{{ page.title }}</strong>
          <small>{{ sections.get(page.section) ?? page.section }} · {{ ROLES[page.min_role] }}</small>
        </div>
        <span class="apc-kind" :class="`is-${page.kind}`">{{ KIND_LABELS[page.kind] }}</span>
        <div class="apc-page-actions">
          <template v-if="!compact">
            <a v-if="pageTarget(page).href" class="icon-button apc-icon-text" :href="pageTarget(page).href!" target="_blank" rel="noopener noreferrer"><ExternalLink aria-hidden="true" />打开</a>
            <RouterLink v-else-if="pageTarget(page).to" class="icon-button apc-icon-text" :to="pageTarget(page).to!">打开</RouterLink>
          </template>
          <button v-if="canEdit" type="button" class="icon-button apc-icon-text" :aria-label="`编辑入口：${page.title}`" @click="$emit('edit', page)"><Pencil aria-hidden="true" />编辑入口</button>
          <button v-if="canEdit && !compact" type="button" class="icon-button apc-icon-text im-danger" :aria-label="`移除入口：${page.title}`" @click="$emit('remove', page)"><Trash2 aria-hidden="true" />移除</button>
        </div>
      </li>
    </ul>
    <div class="apc-pages-foot">
      <button v-if="compact" type="button" class="apc-link-button" @click="$emit('all')">管理全部页面与侧栏<ArrowRight aria-hidden="true" /></button>
      <RouterLink v-else-if="canEdit" class="apc-link-button" to="/settings/navigation">在“侧栏与页面”里调整分组、位置与顺序<ArrowRight aria-hidden="true" /></RouterLink>
      <span v-if="!canEdit" class="form-help">你可以查看；只有 owner 能添加、编辑或移除入口。</span>
    </div>
  </section>
</template>
