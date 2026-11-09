<script setup lang="ts">
import { FolderGit2, Lock, TriangleAlert } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { count, runState, windowLabel, UNKNOWN } from './format'

// 仓库概览表（T-0176）：点选一行（或按行里的仓库名按钮）只切换右侧的权限快照，不改变统计。
// 子数字为 null 时显示“—”并说明原因；“最近运行”是这个仓库最近一次 Actions 运行，不与任何 PR 关联。
defineProps<{
  rows: Schema['RepoOverviewRow'][]
  selectedId?: string
  range: '24h' | '7d'
  total: number | null
  hasMore: boolean
  loadingMore: boolean
  sync?: Schema['SyncState']
}>()
const emit = defineEmits<{ select: [id: string]; more: [] }>()
const UNKNOWN_HINT = '不能确定：缺少读取权限、仓库不可见或数据不完整'
function accessText(access: Schema['RepoOut']['access']) {
  return access === 'missing_permission' ? '部分内容缺少读取权限' : '仓库不存在或 Token 未获授权'
}
</script>

<template>
  <section class="ws-panel dv-panel dv-repos" aria-labelledby="dv-repos-title">
    <div class="dv-panel-head">
      <h2 id="dv-repos-title">仓库概览</h2>
      <span class="ws-meta"><template v-if="total !== null">共 {{ total }} 个 · </template>点选一行查看读取权限</span>
    </div>
    <p v-if="!rows.length" class="ws-state-line">
      {{ sync?.reason === 'rate_limited' || sync?.status === 'unavailable' ? '暂无可用的缓存数据，请等待同步恢复。' : '所选范围没有已同步的仓库。' }}
    </p>
    <div v-else class="ws-table-wrap">
      <table class="ws-table dv-table" aria-labelledby="dv-repos-title">
        <thead>
          <tr>
            <th scope="col">仓库</th>
            <th scope="col" class="dv-num">开放 Issue</th>
            <th scope="col" class="dv-num">开放 PR</th>
            <th scope="col" class="dv-col-run">最近运行</th>
            <th scope="col" class="dv-col-time">更新</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.repo.id" :aria-selected="row.repo.id === selectedId" @click="emit('select', row.repo.id)">
            <td>
              <div class="dv-repo">
                <FolderGit2 class="dv-repo-icon" aria-hidden="true" />
                <div class="dv-repo-text">
                  <button type="button" class="dv-repo-name" :aria-pressed="row.repo.id === selectedId" @click.stop="emit('select', row.repo.id)">{{ row.repo.full_name }}</button>
                  <span v-if="row.repo.description" class="dv-repo-desc">{{ row.repo.description }}</span>
                  <span v-if="row.repo.access !== 'ok'" class="dv-repo-warn"><TriangleAlert aria-hidden="true" />{{ accessText(row.repo.access) }}</span>
                  <span v-if="row.repo.private" class="dv-repo-flag"><Lock aria-hidden="true" />私有</span>
                </div>
              </div>
            </td>
            <td class="dv-num" :title="row.open_issue_count === null ? UNKNOWN_HINT : undefined">{{ count(row.open_issue_count) }}</td>
            <td class="dv-num" :title="row.open_pull_count === null ? UNKNOWN_HINT : undefined">{{ count(row.open_pull_count) }}</td>
            <td class="dv-col-run">
              <span v-if="runState(row.latest_run)" class="ws-state" :class="`dv-tone-${runState(row.latest_run)!.tone}`">{{ runState(row.latest_run)!.label }}</span>
              <span v-else class="dv-muted">{{ UNKNOWN }}</span>
              <span v-if="row.failed_run_count === null" class="dv-cell-note" :title="UNKNOWN_HINT">失败数未知</span>
              <span v-else-if="row.failed_run_count > 0" class="dv-cell-note">{{ windowLabel(range) }}失败 {{ row.failed_run_count }} 次</span>
            </td>
            <td class="dv-col-time">
              <time v-if="row.repo.source_updated_at" :datetime="row.repo.source_updated_at">{{ formatTime(row.repo.source_updated_at) }}</time>
              <time v-else :datetime="row.repo.synced_at">同步于 {{ formatTime(row.repo.synced_at) }}</time>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="hasMore" class="dv-more"><button type="button" class="secondary-button" :disabled="loadingMore" @click="emit('more')">加载更多仓库</button></div>
  </section>
</template>
