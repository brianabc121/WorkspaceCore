<script setup lang="ts">
import { computed } from 'vue'
import type { Schema } from '../../services/api'
import type { GitHubKind, GitHubRecord } from './queries'
import { formatTime } from '../time'
import ExternalLink from '../../components/ExternalLink.vue'
const props = defineProps<{ item: GitHubRecord; kind: GitHubKind; compact?: boolean }>()
const repo = computed(() => props.kind === 'repos' ? props.item as Schema['RepoOut'] : null)
const issue = computed(() => props.kind === 'issues' ? props.item as Schema['IssueOut'] : null)
const pull = computed(() => props.kind === 'pulls' ? props.item as Schema['PullOut'] : null)
const run = computed(() => props.kind === 'runs' ? props.item as Schema['RunOut'] : null)
const title = computed(() => repo.value?.full_name ?? issue.value?.title ?? pull.value?.title ?? run.value?.display_title ?? '')
</script>
<template><article :class="compact ? 'github-compact-record' : 'panel form-panel resource-record'"><component :is="compact ? 'h3' : 'h2'">{{ title }}</component><template v-if="repo"><p v-if="!compact" class="note-text">{{ repo.description }}</p><p>{{ repo.private ? '私有仓库' : '公开仓库' }} · {{ repo.archived ? '已归档' : '活跃' }} · {{ repo.default_branch }}</p><p v-if="repo.access !== 'ok'" class="feedback">{{ repo.access === 'missing_permission' ? '仓库缺少读取权限，请管理员更新 Token' : '仓库不存在或 Token 未获授权' }}</p></template><template v-if="issue"><p>{{ issue.repo_full_name }} · #{{ issue.number }} · {{ issue.state }} · {{ issue.author }}</p><p v-if="!compact">{{ issue.labels.join(' · ') }} · {{ issue.comments }} 条评论</p></template><template v-if="pull"><p>{{ pull.repo_full_name }} · #{{ pull.number }} · {{ pull.merged ? '已合并' : pull.draft ? '草稿' : pull.state }}</p><p v-if="!compact">{{ pull.head_ref }} → {{ pull.base_ref }} · {{ pull.author }} · {{ pull.requested_reviewers.join('、') }}</p></template><template v-if="run"><p>{{ run.repo_full_name }} · {{ run.name }} #{{ run.run_number }} · {{ run.status }} / {{ run.conclusion ?? '尚无结论' }}</p><p v-if="!compact">{{ run.head_branch }} · {{ run.head_sha.slice(0, 12) }} · {{ run.actor }} · {{ formatTime(run.run_started_at) }}</p><ul v-if="run.failure_summary?.failed_jobs.length" class="github-failures"><li v-for="job in run.failure_summary.failed_jobs" :key="job.name">{{ job.name }}：{{ job.failed_steps.join('、') || '失败步骤未提供' }}</li></ul></template><p class="form-help">GitHub · 更新于 {{ formatTime(item.source_updated_at) }}</p><ExternalLink :href="item.source_url" label="在 GitHub 中打开" /></article></template>
