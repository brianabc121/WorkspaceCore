<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Search, X } from '@lucide/vue'
import { DEFAULT_FILTERS, EVENT_TYPES, Q_MAX, RANGES, sameFilters, type AuditFilterState, type MemberOption } from './queries'

// 审计日志的筛选条（T-0198，效果图 08）：时间范围、操作者、事件类型、关键词。都在服务端、分页之前生效；
// 改任何一项都从第一页重新加载。关键词停顿 300ms 再提交，回车立即提交。
const props = defineProps<{ filters: AuditFilterState; members: MemberOption[] | undefined; membersFailed: boolean }>()
const emit = defineEmits<{ change: [patch: Partial<AuditFilterState>] }>()

// 链接里带的成员已不在成员表里（被移出或成员表读不到）：保留这一项，免得选择框显示成“全部”
const actorOptions = computed(() => {
  const list = props.members ?? []
  const actor = props.filters.actor
  return actor && actor !== 'system' && !list.some((m) => m.id === actor) ? [...list, { id: actor, name: `成员 ${actor.slice(0, 8)}…` }] : list
})

const text = ref(props.filters.q)
let timer: ReturnType<typeof setTimeout> | undefined
function commit() {
  clearTimeout(timer)
  const q = text.value.trim().slice(0, Q_MAX)
  if (q !== props.filters.q) emit('change', { q })
}
function typed() {
  clearTimeout(timer)
  timer = setTimeout(commit, 300)
}
// 后退 / 前进改了地址里的关键词：同步到输入框
watch(() => props.filters.q, (q) => { if (q !== text.value.trim()) { clearTimeout(timer); text.value = q } })
onBeforeUnmount(() => clearTimeout(timer))
const changed = computed(() => !sameFilters(props.filters, DEFAULT_FILTERS))
function reset() { clearTimeout(timer); text.value = ''; emit('change', { ...DEFAULT_FILTERS }) }
// 选择框用 v-model（选项晚到时也能选中）；改动直接交给页面写进地址
const field = <K extends 'range' | 'actor' | 'type'>(key: K) => computed({ get: () => props.filters[key], set: (next: AuditFilterState[K]) => emit('change', { [key]: next }) })
const range = field('range'), actor = field('actor'), type = field('type')
</script>

<template>
  <div class="au-filters">
    <div class="ws-toolbar au-toolbar" role="group" aria-label="筛选审计记录">
      <label class="au-select"><span class="sr-only">时间范围</span>
        <select v-model="range">
          <option v-for="r in RANGES" :key="r.key" :value="r.key">{{ r.label }}</option>
        </select>
      </label>
      <label class="au-select"><span class="sr-only">操作者</span>
        <select v-model="actor">
          <option value="">全部操作者</option>
          <option value="system">系统</option>
          <option v-for="m in actorOptions" :key="m.id" :value="m.id">{{ m.name }}</option>
        </select>
      </label>
      <label class="au-select"><span class="sr-only">事件类型</span>
        <select v-model="type">
          <option value="">全部事件类型</option>
          <option v-for="t in EVENT_TYPES" :key="t.prefix" :value="t.prefix">{{ t.label }}</option>
        </select>
      </label>
      <label class="ws-search au-search"><Search aria-hidden="true" /><span class="sr-only">搜索审计记录</span>
        <input
          v-model="text" type="search" :maxlength="Q_MAX" placeholder="名称、事件或资源ID" autocomplete="off"
          title="匹配事件名（含中文名）、对象类型、资源 ID，以及记录里保存的名称 / 标题"
          @input="typed" @keydown.enter.prevent="commit" @search="commit"
        />
      </label>
      <button v-if="changed" type="button" class="secondary-button au-reset" @click="reset"><X aria-hidden="true" />清除筛选</button>
    </div>
    <p v-if="filters.q" class="form-help au-hint">关键词匹配事件名（含中文名）、对象类型、资源 ID 和记录里保存的名称 / 标题，不匹配对象现在的名称。</p>
    <p v-if="membersFailed" class="form-help au-hint">成员列表暂时读不到，操作者只能选“全部”或“系统”。</p>
  </div>
</template>
