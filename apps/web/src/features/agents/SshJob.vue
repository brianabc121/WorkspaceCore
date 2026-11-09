<script setup lang="ts">
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { SSH_JOB_KIND, SSH_JOB_STATUS, SSH_STEP, SSH_STEP_STATUS } from './ssh'

// 一次 SSH 任务（初始化服务器、部署、卸载）的逐步进度与脱敏后的命令输出（T-0090、T-0147）。
// 步骤与失败原因都来自任务记录本身；没登记文字的新步骤直接显示步骤名，不猜。
defineProps<{ job: Schema['AgentSshJobOut'] }>()
const label = (key: string) => (SSH_STEP as Record<string, string>)[key] ?? key
</script>

<template>
  <section class="im-group-form ssh-job" :aria-label="`${SSH_JOB_KIND[job.kind]}进度`">
    <p><strong>{{ SSH_JOB_KIND[job.kind] }}：{{ SSH_JOB_STATUS[job.status] }}</strong><span class="form-help"> · 开始于 {{ formatTime(job.started_at) }}<template v-if="job.finished_at"> · 结束于 {{ formatTime(job.finished_at) }}</template></span></p>
    <ol class="im-contact-list ssh-steps">
      <li v-for="step in job.steps" :key="step.key" :data-status="step.status">
        <span>{{ label(step.key) }}<template v-if="step.detail">：{{ step.detail }}</template></span>
        <span class="badge">{{ SSH_STEP_STATUS[step.status] }}</span>
      </li>
    </ol>
    <p v-if="job.status === 'succeeded' && job.kind === 'install'" role="status">部署完成，实例已连上工作台。</p>
    <p v-else-if="job.status === 'succeeded' && job.kind === 'prepare'" role="status">服务器初始化完成：可以在这台服务器上部署智能体了。</p>
    <p v-else-if="job.status === 'succeeded'" role="status">卸载完成。</p>
    <p v-else-if="job.status === 'failed'" role="alert">{{ job.error_message ?? '失败，详见日志' }}<template v-if="job.error_code">（{{ job.error_code }}）</template></p>
    <p v-else-if="job.status === 'interrupted'" role="alert">任务被中断（工作台重启过），可以重新开始。</p>
    <details v-if="job.log" :open="job.status === 'failed'"><summary>命令输出（密码、令牌、私钥已替换成 ***）</summary><pre class="ai-arguments">{{ job.log }}</pre></details>
  </section>
</template>
