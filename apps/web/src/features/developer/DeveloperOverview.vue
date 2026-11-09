<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { History, Plug, Search } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { errorMessage, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { safeUrl } from '../../widgets/registry'
import RepositoryOverviewTable from './RepositoryOverviewTable.vue'
import GitHubAttentionPanel from './GitHubAttentionPanel.vue'
import GitHubFailedRuns from './GitHubFailedRuns.vue'
import RepositoryPermissionPanel from './RepositoryPermissionPanel.vue'
import { kindLabel } from './format'
import { useRepositoryPermissions, type DeveloperOverviewFilters } from './queries'

// 仓库标签的总览（T-0176）：主区是仓库表、开放 PR 与最近更新；侧栏是失败运行、所选仓库的权限快照与同步状态。
// ≥1440px 主区 + 右侧栏；1024–1439px 侧栏移到下方（两列）；更窄单列。
// 选中行只改变权限快照的请求；搜索、仓库与时间窗才改变总览（统计跟着变）。
const props = defineProps<{
  scope: string
  filters: DeveloperOverviewFilters
  denied: boolean
  sync?: Schema['SyncState']
  pages: Schema['DeveloperOverviewOut'][]
  pending: boolean
  error: unknown
  hasMore: boolean
  loadingMore: boolean
  choices: Schema['RepoOut'][]
  choicesTruncated: boolean
}>()
const emit = defineEmits<{ 'update:filters': [value: DeveloperOverviewFilters]; more: []; retry: [] }>()
const auth = useAuthStore()

const first = computed(() => props.pages[0])
const rows = computed(() => {
  const seen = new Set<string>()
  return props.pages.flatMap((page) => page.repos.items).filter((row) => (seen.has(row.repo.id) ? false : (seen.add(row.repo.id), true)))
})

// 名称搜索：输入停顿 300ms 后生效
const term = ref(props.filters.q ?? '')
let timer: ReturnType<typeof setTimeout> | undefined
watch(term, (value) => {
  clearTimeout(timer)
  timer = setTimeout(() => {
    const q = value.trim().slice(0, 100)
    if ((props.filters.q ?? '') !== q) emit('update:filters', { ...props.filters, q: q || undefined })
  }, 300)
})
onBeforeUnmount(() => clearTimeout(timer))
function setRepo(event: Event) { const value = (event.target as HTMLSelectElement).value; emit('update:filters', { ...props.filters, repo_id: value || undefined }) }
function setRange(event: Event) { emit('update:filters', { ...props.filters, range: (event.target as HTMLSelectElement).value === '24h' ? '24h' : '7d' }) }

// 选中的仓库：默认第一行；选中的那行不在当前结果里时回到第一行
const chosen = ref<string>()
const selected = computed(() => rows.value.find((row) => row.repo.id === chosen.value) ?? rows.value[0])
const selectedId = computed(() => (props.denied ? undefined : selected.value?.repo.id))
const permissions = useRepositoryPermissions(props.scope, selectedId, () => !props.denied)

const recent = computed(() => first.value?.recent_updates ?? [])
const syncFacts = computed(() => {
  const s = props.sync
  if (!s) return null
  const state = s.status === 'unauthorized' ? '授权失效' : s.status === 'not_configured' ? '未配置' : s.reason === 'rate_limited' ? '限流，等待恢复' : s.status === 'unavailable' ? (s.reason === 'not_synced' ? '尚未完成首次同步' : '暂不可用') : s.status === 'stale' ? '可能过期' : '正常'
  return { state, tone: s.status === 'ok' ? 'success' : s.status === 'unauthorized' ? 'danger' : 'warning', asOf: s.as_of, retryAt: s.reason === 'rate_limited' ? s.retry_at ?? null : null }
})
</script>

<template>
  <form class="ws-toolbar dv-toolbar" role="search" aria-label="仓库筛选" @submit.prevent>
    <label class="ws-search"><Search aria-hidden="true" /><span class="sr-only">搜索仓库</span><input v-model="term" type="search" maxlength="100" placeholder="搜索仓库名称" :disabled="denied"></label>
    <label class="dv-select"><span class="sr-only">仓库范围</span><select :value="filters.repo_id ?? ''" :disabled="denied" @change="setRepo"><option value="">全部仓库</option><option v-for="repo in choices" :key="repo.id" :value="repo.id">{{ repo.full_name }}</option><option v-if="choicesTruncated" disabled value="__more">只列出前 100 个，其余请搜索</option></select></label>
    <label class="dv-select"><span class="sr-only">失败运行时间窗</span><select :value="filters.range" :disabled="denied" @change="setRange"><option value="7d">最近 7 天</option><option value="24h">最近 24 小时</option></select></label>
  </form>

  <section v-if="denied" class="ws-panel ws-panel-body dv-empty">
    <Plug aria-hidden="true" />
    <p v-if="sync?.status === 'unauthorized'">授权失效期间不显示仓库、Pull requests、运行与权限快照。</p>
    <p v-else-if="sync?.status === 'not_configured'">还没有可用的 GitHub 连接，接入后这里会显示已同步的仓库。</p>
    <p v-else role="alert">你没有查看这些 GitHub 数据的权限。</p>
    <RouterLink v-if="auth.canManage && sync?.status !== undefined && sync.status !== 'ok'" to="/settings/integrations">前往 GitHub 连接设置</RouterLink>
    <p v-else-if="sync?.status === 'unauthorized' || sync?.status === 'not_configured'" class="ws-meta">请联系管理员在连接设置中处理。</p>
  </section>

  <div v-else class="dv-layout">
    <div class="dv-main">
      <p v-if="pending" role="status" class="ws-panel ws-state-line">正在加载仓库概览…</p>
      <div v-else-if="error && !rows.length" class="ws-panel ws-panel-body dv-error">
        <p role="alert">{{ errorMessage(error) }}</p>
        <button type="button" class="secondary-button" @click="emit('retry')">重试</button>
      </div>
      <template v-else>
        <p v-if="error" role="alert" class="feedback">刷新失败，当前显示上次读取的数据：{{ errorMessage(error) }}</p>
        <RepositoryOverviewTable
          :rows="rows" :selected-id="selectedId" :range="filters.range" :total="first?.repos.total_estimate ?? null"
          :has-more="hasMore" :loading-more="loadingMore" :sync="sync"
          @select="(id: string) => (chosen = id)" @more="emit('more')"
        />
        <GitHubAttentionPanel :pulls="first?.attention_pulls ?? []" :open-count="first?.summary.open_pull_count ?? null" />
        <section class="ws-panel dv-panel" aria-labelledby="dv-recent-title">
          <div class="dv-panel-head"><h2 id="dv-recent-title"><History aria-hidden="true" />最近更新</h2><span class="ws-meta">同步到的最近变化，不是操作记录</span></div>
          <ol v-if="recent.length" class="dv-recent">
            <li v-for="item in recent" :key="item.id">
              <span class="dv-recent-dot" aria-hidden="true" />
              <div class="dv-recent-main">
                <a v-if="safeUrl(item.source_url)" :href="item.source_url" target="_blank" rel="noopener noreferrer" class="dv-recent-title">{{ item.title || '（无标题）' }}</a>
                <span v-else class="dv-recent-title">{{ item.title || '（无标题）' }}</span>
                <span class="dv-sub">{{ kindLabel(item.kind) }}<template v-if="item.kind !== 'repo' && item.repo_full_name"> · {{ item.repo_full_name }}</template></span>
              </div>
              <time class="dv-recent-time" :datetime="item.updated_at">{{ item.time_basis === 'synced_at' ? '同步于' : '更新于' }} {{ formatTime(item.updated_at) }}</time>
            </li>
          </ol>
          <p v-else class="ws-state-line">所选范围暂无已同步的更新。</p>
        </section>
      </template>
    </div>

    <aside class="dv-side" aria-label="运行与权限">
      <GitHubFailedRuns :runs="first?.failed_runs ?? []" :failed-count="first?.summary.failed_run_count ?? null" :range="filters.range" :loading="pending" />
      <RepositoryPermissionPanel
        :repo="selected?.repo ?? null" :evidence="permissions.data.value" :pending="!!selectedId && permissions.isPending.value"
        :error="permissions.error.value" :can-manage="auth.canManage" @retry="permissions.refetch()"
      />
      <section v-if="syncFacts" class="ws-panel dv-panel dv-sync" aria-labelledby="dv-sync-title">
        <div class="dv-panel-head"><h2 id="dv-sync-title">同步状态</h2><span class="ws-state" :class="`dv-tone-${syncFacts.tone}`">{{ syncFacts.state }}</span></div>
        <dl class="ws-facts">
          <dt>最近成功同步</dt><dd><time v-if="syncFacts.asOf" :datetime="syncFacts.asOf">{{ formatTime(syncFacts.asOf) }}</time><template v-else>—</template></dd>
          <template v-if="syncFacts.retryAt"><dt>预计恢复</dt><dd><time :datetime="syncFacts.retryAt">{{ formatTime(syncFacts.retryAt) }}</time></dd></template>
        </dl>
        <p class="dv-note">“刷新 GitHub 数据”只重新读取工作台已同步的数据；请求同步、测试连接请到连接设置（管理员）。</p>
      </section>
    </aside>
  </div>
</template>
