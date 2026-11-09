<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { Bot, Eye, FolderKanban, MoreHorizontal, PlayCircle, TriangleAlert } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { relativeTime } from '../../widgets/home/format'
import { groupState } from './projectAgents'

// 项目卡片（T-0168，效果图 02-projects；规范第 5 节）：数字都来自 ProjectOut，不在卡片里另外请求。
// 进度 = 已完成 / (已完成 + 未完成)；待查看仍算已完成，不倒扣进度；没有任务显示“尚无任务”，不显示 100%。
const props = defineProps<{ project: Schema['ProjectOut']; writable: boolean; busy: boolean }>()
const emit = defineEmits<{ edit: []; archive: []; remove: []; members: [] }>()
const total = computed(() => props.project.open_tasks + props.project.done_tasks)
const percent = computed(() => (total.value ? Math.round((props.project.done_tasks / total.value) * 100) : null))
const states = computed(() => props.project.task_states)
const flags = computed(() => {
  const s = states.value
  if (!s) return []
  return [
    { key: 'running', label: `进行中 ${s.running}`, show: s.running > 0, icon: PlayCircle },
    { key: 'awaiting_view', label: `待查看 ${s.awaiting_view}`, show: s.awaiting_view > 0, icon: Eye },
    { key: 'blocked', label: `阻塞 ${s.blocked}`, show: s.blocked > 0, icon: TriangleAlert },
  ].filter((f) => f.show)
})
const attention = computed(() => !!states.value && states.value.awaiting_view + states.value.blocked > 0)

// “更多操作”菜单：选了一项、按 Esc 或点到菜单外面都收起
const menu = ref<HTMLDetailsElement | null>(null)
const closeMenu = () => { if (menu.value) menu.value.open = false }
const outside = (event: PointerEvent) => { if (menu.value?.open && !menu.value.contains(event.target as Node)) closeMenu() }
document.addEventListener('pointerdown', outside)
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside))
</script>

<template>
  <section class="ws-panel project-card" :class="{ 'needs-attention': attention, 'is-archived': project.archived }" role="region" :aria-label="`项目 ${project.title}`">
    <header class="project-card-head">
      <span class="project-icon" aria-hidden="true"><FolderKanban /></span>
      <div class="project-card-title">
        <h2><RouterLink :to="`/projects/${project.id}`" :aria-label="`打开项目 ${project.title}`">{{ project.title }}</RouterLink></h2>
        <p v-if="project.description" class="project-description">{{ project.description }}</p>
      </div>
      <span v-if="project.archived" class="badge">已归档</span>
      <details ref="menu" class="project-menu" @keydown.esc="closeMenu">
        <summary :aria-label="`项目 ${project.title} 的更多操作`"><MoreHorizontal aria-hidden="true" /></summary>
        <div class="project-menu-list" @click="closeMenu">
          <RouterLink :to="{ path: '/tasks/timeline', query: { project: project.id } }">时间轴</RouterLink>
          <RouterLink :to="{ path: '/knowledge/docs', query: { linked_resource_id: project.id } }">相关文档</RouterLink>
          <template v-if="writable">
            <button type="button" :aria-label="`编辑项目 ${project.title}`" :disabled="busy" @click="emit('edit')">编辑</button>
            <button type="button" :aria-label="`${project.archived ? '恢复' : '归档'}项目 ${project.title}`" :disabled="busy" @click="emit('archive')">{{ project.archived ? '恢复' : '归档' }}</button>
            <button type="button" class="danger" :aria-label="`删除项目 ${project.title}`" :disabled="busy" @click="emit('remove')">删除</button>
          </template>
        </div>
      </details>
    </header>

    <div v-if="percent !== null" class="project-progress">
      <div class="project-progress-label"><span>进度 {{ project.done_tasks }}/{{ total }}</span><span>{{ percent }}%</span></div>
      <div class="progress-track" role="progressbar" :aria-label="`${project.title} 完成进度`" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="percent"><span :style="{ width: `${percent}%` }"></span></div>
    </div>
    <p v-else class="project-progress-empty">尚无任务</p>

    <div class="project-flags">
      <span v-for="flag in flags" :key="flag.key" class="ws-state" :class="`state-${flag.key}`"><component :is="flag.icon" aria-hidden="true" />{{ flag.label }}</span>
      <span v-if="!flags.length && states && total" class="project-calm">没有进行中或需要关注的任务</span>
      <span v-if="!states" class="project-calm">状态计数暂时读不到</span>
    </div>

    <footer class="project-card-foot">
      <p class="project-agents" :aria-label="`${project.title} 的智能体`"><Bot aria-hidden="true" />智能体：{{ project.agents.length ? project.agents.map((a) => a.name).join('、') : '无' }} · {{ groupState(project.im_group).short }}</p>
      <div class="project-card-links">
        <RouterLink :to="{ path: '/tasks', query: { project_id: project.id } }">查看任务</RouterLink>
        <button type="button" class="link-button" :aria-label="`项目 ${project.title} 的成员`" @click="emit('members')">成员</button>
        <span v-if="project.last_activity_at" class="project-updated">最近更新 {{ relativeTime(project.last_activity_at) }}</span>
      </div>
    </footer>
  </section>
</template>
