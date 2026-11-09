<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useIsFetching, useQueryClient } from '@tanstack/vue-query'
import { CircleAlert, CircleCheck, CircleDot, FolderGit2, GitPullRequest, Plug, RefreshCw, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, isAccessError, type Schema } from '../../services/api'
import { formatTime } from '../time'
import DataState from '../../components/DataState.vue'
import GitHubRecord from './GitHubRecord.vue'
import DeveloperOverview from './DeveloperOverview.vue'
import { count, windowLabel } from './format'
import { githubSync, permissionsKey, useGitHubList, useGitHubOverview, worstSync, type DeveloperOverviewFilters, type GitHubFilters, type GitHubKind } from './queries'
import '../workspace/workspace-pages.css'
import './developer.css'

// 开发者页（T-0176，二批四页改造效果图 03）：统计四卡 + 仓库 / Issues / Pull requests / Actions 四个标签。
// 只读本地 GitHub 投影：“刷新 GitHub 数据”只重新读取；请求同步、测试连接仍在连接设置页，由管理员操作。
// 换用户或工作空间时整页重建（DeveloperPage 的 key），查询键都带用户与工作空间。
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, cache = useQueryClient()
const kinds: Record<GitHubKind, string> = { repos: '仓库', issues: 'Issues', pulls: 'Pull requests', runs: 'Actions' }
const kind = ref<GitHubKind>('repos')

// 仓库标签：总览（统计、仓库表、开放 PR、失败运行、最近更新）
const overviewFilters = ref<DeveloperOverviewFilters>({ range: '7d' })
const overview = useGitHubOverview(scope, overviewFilters)
const pages = computed(() => overview.data.value?.pages ?? [])
// 选中的仓库被移出范围（404）：回到全部仓库，不当成失权
const missingRepo = computed(() => overview.error.value instanceof ApiError && overview.error.value.status === 404 && !!overviewFilters.value.repo_id)
watch(missingRepo, (gone) => { if (gone) overviewFilters.value = { ...overviewFilters.value, repo_id: undefined } })

// 其余三个标签：原来的列表、筛选与分页
const repository = ref(''), state = ref<'open' | 'closed' | 'all'>('open'), title = ref(''), conclusion = ref<'' | 'failure' | 'success'>('')
const filters = ref<GitHubFilters>({})
const list = useGitHubList(kind, scope, filters, 'page', () => kind.value !== 'repos')
// 仓库下拉的选项（最多 100 个；更多时请用名称搜索）
const choiceQuery = useGitHubList('repos', scope, { limit: 100 }, 'choices')

const accessLost = computed(() => (isAccessError(overview.error.value) && !missingRepo.value) || (kind.value !== 'repos' && isAccessError(list.error.value)))
// 失权（401 / 403）时不再沿用上次的同步状态，免得显示“已同步”
const sync = computed(() => (accessLost.value ? undefined : worstSync([...pages.value.map((p) => p.sync), ...(kind.value === 'repos' ? [] : [githubSync(list.data.value?.pages)])])))
// 失权或未配置：不显示任何旧数据
const denied = computed(() => accessLost.value || ['unauthorized', 'not_configured'].includes(sync.value?.status ?? ''))
watch(denied, (value) => { if (value) cache.removeQueries({ queryKey: permissionsKey(user, scope) }) }, { immediate: true })

const first = computed<Schema['DeveloperOverviewOut'] | undefined>(() => (denied.value ? undefined : pages.value[0]))
const summary = computed(() => first.value?.summary)
const choices = computed(() => (denied.value ? [] : ((choiceQuery.data.value?.pages[0]?.items ?? []) as Schema['RepoOut'][])))
const choicesTruncated = computed(() => !!choiceQuery.data.value?.pages[0]?.next_cursor)
const items = computed(() => (denied.value ? [] : list.data.value?.pages.flatMap((page) => page.items) ?? []))

const stats = computed(() => {
  const s = summary.value, pending = overview.isPending.value && !denied.value
  const value = (n: number | null | undefined) => (pending ? '…' : count(n))
  return [
    { key: 'repos', label: '仓库', value: value(s?.repo_count), note: '已同步范围', icon: FolderGit2, tone: 'tone-blue' },
    { key: 'issues', label: '开放 Issues', value: value(s?.open_issue_count), note: '当前开放 · 已同步范围', icon: CircleDot, tone: 'tone-indigo' },
    { key: 'pulls', label: '开放 PR', value: value(s?.open_pull_count), note: '当前开放 · 已同步范围', icon: GitPullRequest, tone: 'tone-amber' },
    { key: 'runs', label: '失败运行', value: value(s?.failed_run_count), note: `${windowLabel(overviewFilters.value.range)}更新 · 已同步范围`, icon: TriangleAlert, tone: 'tone-red' },
  ]
})
const scopeName = computed(() => {
  const f = overviewFilters.value
  if (f.repo_id) return choices.value.find((r) => r.id === f.repo_id)?.full_name ?? '所选仓库'
  return f.q ? `名称包含“${f.q}”的仓库` : '全部仓库'
})

const banner = computed(() => {
  const s = sync.value
  if (!s) return null
  const cached = s.as_of ? `当前显示 ${formatTime(s.as_of)} 同步的缓存` : '暂无可用的缓存数据'
  if (s.status === 'unauthorized') return { tone: 'danger', icon: CircleAlert, text: 'GitHub 授权已失效，请管理员重新连接；在此之前不显示已同步的数据' }
  if (s.status === 'not_configured') return { tone: 'muted', icon: Plug, text: '尚未配置可用的 GitHub 连接' }
  if (s.reason === 'rate_limited') return { tone: 'warning', icon: TriangleAlert, text: 'GitHub 限流中' + (s.retry_at ? `，预计 ${formatTime(s.retry_at)} 恢复` : '，恢复时间待确认') + ` · ${cached}` }
  if (s.status === 'unavailable') return { tone: 'warning', icon: TriangleAlert, text: s.reason === 'not_synced' ? 'GitHub 尚未完成首次同步，同步完成后显示数据' : 'GitHub 暂不可用，请等待同步或稍后重试' }
  if (s.status === 'stale') return { tone: 'warning', icon: TriangleAlert, text: (s.reason === 'source_unavailable' ? '最近一次访问 GitHub 失败' : s.reason === 'sync_overdue' ? '同步已逾期' : '数据可能已过期') + ` · ${cached}` }
  return { tone: 'success', icon: CircleCheck, text: `GitHub 已同步 · 最近同步 ${formatTime(s.as_of)} · 只读访问` }
})

const fetching = useIsFetching({ queryKey: ['developer', user, scope] })
// 刷新只重新读取本地投影（GET），不请求同步、不测试连接
function refresh() { void cache.invalidateQueries({ queryKey: ['developer', user, scope] }) }
function apply() { filters.value = { ...(repository.value ? { repo_id: repository.value } : {}), state: state.value, ...(title.value.trim() ? { q: title.value.trim() } : {}), ...(conclusion.value ? { conclusion: conclusion.value } : {}) } }
function change(value: GitHubKind) { kind.value = value; repository.value = ''; state.value = 'open'; title.value = ''; conclusion.value = ''; filters.value = {} }
</script>

<template>
  <header class="ws-header">
    <div>
      <h1>开发者</h1>
      <p>GitHub 仓库、Issues、Pull requests 与 Actions 的只读投影，数据来自工作台的定时同步。</p>
    </div>
    <div class="ws-actions">
      <button type="button" class="secondary-button" :disabled="fetching > 0" @click="refresh"><RefreshCw aria-hidden="true" />刷新 GitHub 数据</button>
      <RouterLink v-if="auth.canManage" to="/settings/integrations" class="secondary-button"><Plug aria-hidden="true" />GitHub 连接设置</RouterLink>
    </div>
  </header>

  <div v-if="banner" class="dv-banner" :class="`tone-${banner.tone}`">
    <component :is="banner.icon" aria-hidden="true" />
    <p role="status">{{ banner.text }}</p>
    <RouterLink v-if="auth.canManage && ['unauthorized', 'not_configured'].includes(sync?.status ?? '')" to="/settings/integrations" class="dv-banner-link">重新连接 GitHub</RouterLink>
  </div>

  <section class="dv-stats" aria-label="已同步范围的统计">
    <div v-for="stat in stats" :key="stat.key" class="dv-stat" :class="stat.tone">
      <span class="dv-stat-icon"><component :is="stat.icon" aria-hidden="true" /></span>
      <span class="dv-stat-text">
        <span class="dv-stat-label">{{ stat.label }}</span>
        <strong class="dv-stat-value" :aria-label="`${stat.label}：${stat.value === '—' ? '未知' : stat.value}`">{{ stat.value }}</strong>
        <span class="dv-stat-note">{{ stat.note }}</span>
      </span>
    </div>
  </section>
  <p class="dv-scope">
    统计范围：{{ scopeName }}（已同步到工作台的数据）<template v-if="first?.partial">。部分仓库缺少读取权限、仓库不可见、尚未同步或运行缺少时间，标“—”的数字未知，不按 0 计。</template>
  </p>

  <div class="dv-tabs" role="group" aria-label="开发者视图">
    <button v-for="(label, value) in kinds" :key="value" type="button" :aria-pressed="kind === value" @click="change(value)">{{ label }}</button>
  </div>

  <DeveloperOverview
    v-if="kind === 'repos'"
    v-model:filters="overviewFilters"
    :scope="scope"
    :denied="denied"
    :sync="sync"
    :pages="denied ? [] : pages"
    :pending="overview.isPending.value"
    :error="missingRepo ? null : overview.error.value"
    :has-more="overview.hasNextPage.value && !denied"
    :loading-more="overview.isFetchingNextPage.value"
    :choices="choices"
    :choices-truncated="choicesTruncated"
    @more="overview.fetchNextPage()"
    @retry="overview.refetch()"
  />
  <template v-else>
    <form class="ws-panel ws-panel-body dv-filters" aria-label="GitHub 筛选" @submit.prevent="apply">
      <label>GitHub 仓库<select v-model="repository"><option value="">全部仓库</option><option v-for="repo in choices" :key="repo.id" :value="repo.id">{{ repo.full_name }}</option></select></label>
      <template v-if="kind !== 'runs'">
        <label>GitHub 状态<select v-model="state"><option value="open">Open</option><option value="closed">Closed</option><option value="all">全部状态</option></select></label>
        <label>GitHub 标题搜索<input v-model="title" maxlength="100"></label>
      </template>
      <label v-else>Actions 结论<select v-model="conclusion"><option value="">全部结论</option><option value="failure">失败</option><option value="success">成功</option></select></label>
      <button class="secondary-button">应用 GitHub 筛选</button>
    </form>
    <DataState :loading="list.isPending.value" :error="list.error.value" :empty="!items.length && sync?.status === 'ok' && !sync?.reason" empty-text="没有符合条件的 GitHub 数据。" :stale="!!items.length" @retry="list.isFetchNextPageError.value ? list.fetchNextPage() : list.refetch()" />
    <p v-if="!list.error.value && !items.length && sync?.status === 'stale' && sync.reason !== 'rate_limited'" class="feedback">暂无可用的缓存数据，请等待下一次成功同步。</p>
    <div class="github-list"><GitHubRecord v-for="item in items" :key="item.id" :kind="kind" :item="item" /></div>
    <button v-if="list.hasNextPage.value && !denied" class="secondary-button" :disabled="list.isFetching.value" @click="list.fetchNextPage()">加载更多 GitHub 数据</button>
  </template>
</template>
