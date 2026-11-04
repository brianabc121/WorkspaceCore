<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { BookOpen } from '@lucide/vue'
import type { Space } from './api'

// 知识库分组（T-0099）：全部 / 公共 / 项目知识库 / 智能体知识库。
// 图谱页里点一下只看这个知识库（其余变淡）；手机首页里是链接，打开知识库主页。
// dots：图谱不按知识库着色时不画知识库类型的圆点，免得和类型颜色混淆（T-0102）
const props = withDefaults(defineProps<{ spaces: Space[]; focus?: string | null; links?: boolean; total?: number; dots?: boolean }>(), { focus: null, total: undefined, dots: true })
const emit = defineEmits<{ focus: [id: string | null] }>()
const groups = computed(() => [
  { label: '公共', items: props.spaces.filter(s => s.kind === 'public') },
  { label: '项目知识库', items: props.spaces.filter(s => s.kind === 'project') },
  { label: '智能体知识库', items: props.spaces.filter(s => s.kind === 'agent') },
].filter(g => g.items.length))
function detail(space: Space) {
  if (space.kind === 'agent') return `${space.notes} 篇${space.archived ? ' · 实例已删除，只读' : ''}`
  if (space.kind === 'public') return `${space.agent_access === 'read_write' ? '所有智能体可读写' : space.agent_access === 'read' ? '所有智能体可读' : '智能体不可访问'} · ${space.notes} 篇`
  const agents = space.agents.map(a => a.name)
  return `${agents.length ? agents.join('、') + ' 参与' : '还没有智能体参与'} · ${space.notes} 篇${space.archived ? ' · 已归档' : ''}`
}
</script>

<script lang="ts">
import type { Space as SpaceType } from './api'
export function spaceLink(space: SpaceType) {
  return space.kind === 'agent' && space.instance_id ? `/knowledge/agents/${space.instance_id}` : `/knowledge/spaces/${space.id}`
}
</script>

<template>
  <div class="kb-list" role="list">
    <template v-if="!links">
      <button type="button" class="kb-list-item" :aria-pressed="!focus" role="listitem" @click="emit('focus', null)">
        <BookOpen aria-hidden="true" style="width: 16px; height: 16px; color: var(--kb-muted)" />
        <span class="kb-grow">全部知识库</span><span class="kb-meta kb-num">{{ total ?? spaces.reduce((n, s) => n + s.notes, 0) }}</span>
      </button>
    </template>
    <template v-for="g in groups" :key="g.label">
      <p class="kb-group-label" role="presentation">{{ g.label }}</p>
      <template v-for="s in g.items" :key="s.id">
        <RouterLink v-if="links" :to="spaceLink(s)" class="kb-list-item" role="listitem" style="min-height: 52px">
          <span class="kb-dot" :class="`k-${s.kind}`" aria-hidden="true" />
          <span class="kb-stack kb-grow"><span>{{ s.name }}</span><span class="kb-meta">{{ detail(s) }}</span></span>
        </RouterLink>
        <button v-else type="button" class="kb-list-item" role="listitem" :aria-pressed="focus === s.id" :title="detail(s)" @click="emit('focus', focus === s.id ? null : s.id)">
          <span v-if="dots" class="kb-dot" :class="`k-${s.kind}`" aria-hidden="true" />
          <span class="kb-grow">{{ s.name }}</span><span class="kb-meta kb-num">{{ s.notes }}</span>
        </button>
      </template>
    </template>
  </div>
</template>
