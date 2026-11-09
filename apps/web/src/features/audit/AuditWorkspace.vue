<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQueryClient } from '@tanstack/vue-query'
import { Building2, Crown, Download, RefreshCw } from '@lucide/vue'
import { isAccessError } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { useMediaQuery } from '../../composables/useMediaQuery'
import ManagementPage from '../../components/management/ManagementPage.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import DrawerDetail from '../../components/DrawerDetail.vue'
import AuditFilters from './AuditFilters.vue'
import AuditEventTable from './AuditEventTable.vue'
import AuditDetailPanel from './AuditDetailPanel.vue'
import {
  DEFAULT_FILTERS, canExport, exportError, fetchAuditCsv, filtersFromQuery, filtersToQuery, sameFilters, serverFilters, useAuditEvents, useAuditMembers,
  type AuditEntry, type AuditFilterState,
} from './queries'
import './audit.css'

// 审计日志（T-0198，方案 docs/v2/ai-admin-redesign 计划 C2，效果图 08）：owner / admin 查看当前工作空间的事件记录。
// 筛选写在地址里（?range= / ?actor= / ?type= / ?q=），后退 / 前进能回到之前的条件；详情是 ?id=。
// 所有筛选都在服务端、分页之前生效；只显示“当前已加载 N 条”。没有删除、修改记录的入口。
// 宽屏（1440 以上）详情在右侧一栏，窄屏放进右侧抽屉；关掉后焦点回到打开它的那一行。
// 页面按用户与工作空间重新挂载（见 AuditPage）：换工作空间、换用户不会留下上一位的记录、详情或导出状态。
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user!.id
const route = useRoute(), router = useRouter(), cache = useQueryClient()
const wide = useMediaQuery('(min-width: 1440px)')

const filters = computed(() => filtersFromQuery(route.query))
// 这组条件开始查询的时刻：时间范围按它算，翻页时不变；换条件、点“刷新记录”时更新（从第一页重新读）。
// 和条件一起算出来，条件变化时不会先用旧时刻发一次请求；只改 ?id= 时不变
const reloads = ref(0)
let started = { key: '', at: 0 }
const anchor = computed(() => {
  const key = `${JSON.stringify(filters.value)}#${reloads.value}`
  if (key !== started.key) started = { key, at: Date.now() }
  return started.at
})
const server = computed(() => serverFilters(filters.value, anchor.value))
const events = useAuditEvents(scope, user, server, anchor)
const members = useAuditMembers(scope, user)

const denied = computed(() => isAccessError(events.error.value))
const entries = computed<AuditEntry[]>(() => (denied.value ? [] : events.data.value?.pages.flatMap((page) => page.items) ?? []))
const loaded = (id: string) => entries.value.find((e) => e.id === id)

function change(patch: Partial<AuditFilterState>) {
  const next = { ...filters.value, ...patch }
  if (sameFilters(next, filters.value)) return
  void router.push({ query: filtersToQuery(next, route.query) })
}
const filtered = computed(() => !sameFilters(filters.value, DEFAULT_FILTERS))

function refresh() {
  reloads.value += 1
  void cache.invalidateQueries({ queryKey: ['audit', user, scope, 'detail'] })
}
const retry = () => (events.isFetchNextPageError.value ? events.fetchNextPage() : events.refetch())

// ---- 详情（?id=）
const selected = computed(() => (typeof route.query.id === 'string' ? route.query.id : ''))
let opener: HTMLElement | null = null
function select(id: string, from: HTMLElement | null) {
  opener = from
  if (id !== selected.value) void router.push({ query: { ...route.query, id } })
}
async function close() {
  const query = { ...route.query }
  delete query.id
  await router.replace({ query })
  const back = opener
  opener = null
  await nextTick()
  if (back?.isConnected) back.focus()
}

// ---- 导出当前筛选：同一组服务端条件（不是已加载的这几页），文件只在这一次下载里存在
const exporting = ref(false), exportNotice = ref(''), exportFailure = ref('')
const pending = new Set<string>()
let active = true
onBeforeUnmount(() => { active = false; for (const url of pending) URL.revokeObjectURL(url); pending.clear() })
function save(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  pending.add(url)
  try {
    const link = document.createElement('a')
    link.href = url; link.download = filename; link.rel = 'noopener'
    document.body.append(link); link.click(); link.remove()
  } finally {
    // 浏览器接手下载后释放；离开页面时也会立刻释放
    setTimeout(() => { if (pending.delete(url)) URL.revokeObjectURL(url) }, 1000)
  }
}
async function exportCsv() {
  if (exporting.value) return
  exportNotice.value = ''; exportFailure.value = ''
  const conditions = server.value
  if (!canExport(conditions)) {
    exportFailure.value = '导出需要 31 天以内的时间范围：请先把时间范围改为最近 24 小时、7 天或 30 天。'
    return
  }
  exporting.value = true
  try {
    const file = await fetchAuditCsv(scope, conditions)
    // 等待期间离开了页面、换了工作空间或用户：丢掉这份文件
    if (!active || auth.workspaceId !== scope || auth.user?.id !== user) return
    save(file.blob, file.filename)
    exportNotice.value = `文件已生成，开始下载（${file.filename}）。`
  } catch (cause) {
    if (active) exportFailure.value = exportError(cause)
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <ManagementPage title="审计日志" description="追溯工作空间关键操作，按事件查看相关对象与详情。" aside-label="事件详情">
    <template #context>
      <span class="mg-tag"><Building2 aria-hidden="true" />{{ auth.currentWorkspace?.name }} · 仅当前工作空间</span>
      <span class="mg-role" title="审计日志只有所有者和管理员可以查看与导出"><Crown aria-hidden="true" />所有者 / 管理员</span>
    </template>
    <template #actions>
      <button type="button" class="secondary-button" :disabled="events.isFetching.value && !events.isFetchingNextPage.value" @click="refresh"><RefreshCw aria-hidden="true" />刷新记录</button>
      <button
        type="button" class="secondary-button" :disabled="exporting" :aria-busy="exporting"
        title="按当前筛选（时间范围最长 31 天）在服务器上生成 CSV，最多 10000 行" @click="exportCsv"
      >
        <Download aria-hidden="true" />{{ exporting ? '正在生成文件…' : '导出当前筛选' }}
      </button>
    </template>

    <ActionFeedback :error="exportFailure" :notice="exportNotice" />
    <AuditFilters :filters="filters" :members="members.data.value" :members-failed="!!members.error.value" @change="change" />
    <AuditEventTable
      :entries="entries" :loading="events.isPending.value" :error="events.error.value" :has-more="!denied && !!events.hasNextPage.value"
      :loading-more="events.isFetchingNextPage.value" :selected-id="selected" :filtered="filtered"
      @select="select" @more="events.fetchNextPage()" @retry="retry"
    />

    <template v-if="selected && wide" #aside>
      <section class="ws-panel asv-side au-side">
        <AuditDetailPanel :key="selected" :scope="scope" :user="user" :entry-id="selected" :loaded="loaded" aside @close="close" />
      </section>
    </template>
  </ManagementPage>
  <DrawerDetail v-if="selected && !wide" title="事件详情" @close="close">
    <AuditDetailPanel :key="selected" :scope="scope" :user="user" :entry-id="selected" :loaded="loaded" @close="close" />
  </DrawerDetail>
</template>
