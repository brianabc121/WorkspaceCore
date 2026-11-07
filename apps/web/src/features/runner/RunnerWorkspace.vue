<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { runnerError, useRunnerOverview } from './api'
import OverviewTab from './OverviewTab.vue'
import EnvironmentsTab from './EnvironmentsTab.vue'
import TemplatesTab from './TemplatesTab.vue'
import RunsTab from './RunsTab.vue'
import SettingsTab from './SettingsTab.vue'

// 运行服务器页（T-0140，RUNNER_PLAN 第 11 节，owner / admin）：五个标签，当前标签写在地址里（?tab=），
// 通知里的链接（/runner?tab=environments）直接打开对应的标签。
const TABS = [
  { key: 'overview', name: '总览' },
  { key: 'environments', name: '环境' },
  { key: 'templates', name: '模板' },
  { key: 'runs', name: '运行记录' },
  { key: 'settings', name: '设置' },
] as const
type Tab = typeof TABS[number]['key']
const auth = useAuthStore(), scope = auth.workspaceId!, route = useRoute(), router = useRouter()
const pick = (value: unknown): Tab => TABS.some(t => t.key === value) ? value as Tab : 'overview'
const tab = ref<Tab>(pick(route.query.tab))
watch(() => route.query.tab, (value) => { tab.value = pick(value) })
const stateFilter = ref<string | undefined>(), runsFor = ref<string | undefined>()
function go(next: Tab) { tab.value = next; void router.replace({ query: { ...route.query, tab: next === 'overview' ? undefined : next } }) }
function environments(state?: string) { stateFilter.value = state; go('environments') }
function runs(environmentId: string) { runsFor.value = environmentId; go('runs') }
const overview = useRunnerOverview(scope)
const instances = computed(() => overview.data.value?.instances ?? [])
</script>

<template>
  <div class="form-actions runner-tabs" role="tablist" aria-label="运行服务器">
    <button v-for="t in TABS" :key="t.key" type="button" role="tab" class="secondary-button" :aria-selected="tab === t.key" @click="go(t.key)">{{ t.name }}</button>
  </div>
  <p v-if="overview.isPending.value" role="status">正在加载…</p>
  <p v-else-if="overview.error.value" role="alert">{{ runnerError(overview.error.value) }}</p>
  <div v-else-if="overview.data.value" role="tabpanel" :aria-label="TABS.find(t => t.key === tab)?.name">
    <OverviewTab v-if="tab === 'overview'" :overview="overview.data.value" @environments="environments" @settings="go('settings')" />
    <EnvironmentsTab v-else-if="tab === 'environments'" :instances="instances" :initial-state="stateFilter" @runs="runs" />
    <TemplatesTab v-else-if="tab === 'templates'" />
    <RunsTab v-else-if="tab === 'runs'" :instances="instances" :environment-id="runsFor" @clear="runsFor = undefined" />
    <SettingsTab v-else :host="overview.data.value.host" />
  </div>
</template>
