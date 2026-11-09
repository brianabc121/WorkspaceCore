<script setup lang="ts">
import { CircleAlert, Info } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import ExternalLink from '../../components/ExternalLink.vue'
import { windowLabel } from './format'

// Actions 失败摘要（T-0176）：失败的 job / step 名称来自同步时读的 jobs 接口（不含日志）；
// 没有摘要时如实说明，不填示例里的步骤名。时间窗按 GitHub 更新时间（“近 7 天更新的失败运行”）。
defineProps<{ runs: Schema['RunOut'][]; failedCount: number | null; range: '24h' | '7d'; loading: boolean }>()
</script>

<template>
  <section class="ws-panel dv-panel" aria-labelledby="dv-failed-title">
    <div class="dv-panel-head">
      <h2 id="dv-failed-title">Actions 失败摘要</h2>
      <span class="ws-meta">{{ windowLabel(range) }}更新的失败运行<template v-if="failedCount !== null"> · 共 {{ failedCount }} 个</template></span>
    </div>
    <p v-if="loading" class="ws-state-line">正在读取…</p>
    <template v-else-if="runs.length">
      <article v-for="run in runs" :key="run.id" class="ws-callout dv-run">
        <strong><CircleAlert aria-hidden="true" />{{ run.name || '工作流' }} {{ run.conclusion === 'timed_out' ? '运行超时' : '检查未通过' }}</strong>
        <p class="dv-run-title">{{ run.display_title }}</p>
        <dl class="ws-facts dv-run-facts">
          <dt>仓库</dt><dd>{{ run.repo_full_name }}</dd>
          <dt>失败步骤</dt>
          <dd>
            <ul v-if="run.failure_summary?.failed_jobs.length" class="dv-steps">
              <li v-for="job in run.failure_summary.failed_jobs" :key="job.name">{{ job.name }}：{{ job.failed_steps.join('、') || '失败步骤未同步' }}</li>
            </ul>
            <template v-else>失败步骤未同步，前往 GitHub 查看</template>
          </dd>
          <dt>分支 / 提交</dt><dd>{{ run.head_branch ?? '—' }} · <code>{{ run.head_sha ? run.head_sha.slice(0, 7) : '—' }}</code></dd>
          <dt>更新于</dt><dd><time v-if="run.source_updated_at" :datetime="run.source_updated_at">{{ formatTime(run.source_updated_at) }}</time><template v-else>—</template></dd>
        </dl>
        <ExternalLink :href="run.source_url" label="查看 GitHub 运行" />
      </article>
      <p class="dv-note"><Info aria-hidden="true" />这是 GitHub Actions 的运行结果（CI 检查未通过），不是工作台服务异常。</p>
    </template>
    <p v-else-if="failedCount === null" class="ws-state-line">失败运行数未知：部分仓库缺少 Actions 读取权限、尚未同步或运行缺少时间。</p>
    <p v-else class="ws-state-line">{{ windowLabel(range) }}没有更新的失败运行。</p>
  </section>
</template>
