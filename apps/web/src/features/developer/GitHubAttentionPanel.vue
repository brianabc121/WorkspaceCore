<script setup lang="ts">
import { GitPullRequest } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import ExternalLink from '../../components/ExternalLink.vue'

// 开放的 Pull requests（T-0176，效果图“待审 PR”改为“开放 PR”）：
// 只有 requested_reviewers 有人时才标“已请求审查”；不推断审查结果，也不贴 CI 状态
// （本地投影里没有 PR 的 head commit，不能可靠地和某次运行对上）。
defineProps<{ pulls: Schema['PullOut'][]; openCount: number | null }>()
function badge(pull: Schema['PullOut']) {
  if (pull.requested_reviewers.length) return { label: '已请求审查', tone: 'warning', title: `已请求：${pull.requested_reviewers.join('、')}` }
  if (pull.draft) return { label: '草稿', tone: 'muted', title: undefined }
  return { label: '开放', tone: 'accent', title: undefined }
}
</script>

<template>
  <section class="ws-panel dv-panel" aria-labelledby="dv-pulls-title">
    <div class="dv-panel-head">
      <h2 id="dv-pulls-title">开放的 Pull requests</h2>
      <span class="ws-meta">已请求审查的在前；不代表审查或 CI 结果</span>
    </div>
    <ul v-if="pulls.length" class="dv-pulls">
      <li v-for="pull in pulls" :key="pull.id" class="dv-pull">
        <GitPullRequest class="dv-pull-icon" aria-hidden="true" />
        <div class="dv-pull-main">
          <p class="dv-pull-title">{{ pull.title }} <span class="dv-muted">#{{ pull.number }}</span></p>
          <p class="dv-sub">{{ pull.repo_full_name }} · {{ pull.head_ref || '—' }} → {{ pull.base_ref || '—' }}</p>
        </div>
        <span class="ws-state" :class="`dv-tone-${badge(pull).tone}`" :title="badge(pull).title">{{ badge(pull).label }}</span>
        <span class="dv-pull-meta">
          <span>{{ pull.author ?? '—' }}</span>
          <time v-if="pull.source_updated_at" :datetime="pull.source_updated_at">更新于 {{ formatTime(pull.source_updated_at) }}</time>
        </span>
        <ExternalLink :href="pull.source_url" label="在 GitHub 查看" />
      </li>
    </ul>
    <p v-else-if="openCount === null" class="ws-state-line">开放 PR 数未知：部分仓库缺少 Pull requests 读取权限或尚未同步。</p>
    <p v-else class="ws-state-line">所选范围没有开放的 Pull request。</p>
    <p v-if="pulls.length && openCount !== null && openCount > pulls.length" class="dv-note">共 {{ openCount }} 个开放 PR，这里列出其中 {{ pulls.length }} 个（已请求审查的在前）；全部请看 Pull requests 标签。</p>
  </section>
</template>
