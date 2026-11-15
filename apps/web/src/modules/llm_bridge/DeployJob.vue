<script setup lang="ts">
import { computed } from 'vue'
import { formatTime } from '../../features/time'
import { DEPLOY_KIND, DEPLOY_STATUS, DEPLOY_STEP, DEPLOY_STEP_STATUS, TARGET_KIND, networkName, type DeployJob } from './api'

// 一次部署 / 重新部署 / 重启 / 卸载任务的逐步进度和脱敏后的命令输出。步骤和失败原因都来自任务记录本身。
const props = defineProps<{ job: DeployJob }>()
const text = (value: unknown) => (typeof value === 'string' ? value : '')
// “只删除登记”的卸载：没有连服务器，只有“从工作台去掉登记”一步，说明里写着服务器上还留着什么
const forgotten = computed(() => props.job.kind === 'uninstall' && props.job.steps.length === 1 && props.job.steps[0]?.key === 'unregister')
</script>

<template>
  <section class="lb-job" :aria-label="`${DEPLOY_KIND[job.kind] ?? job.kind}「${job.name}」的进度`">
    <p>
      <strong>{{ DEPLOY_KIND[job.kind] ?? job.kind }}「{{ job.name }}」：{{ DEPLOY_STATUS[job.status] ?? job.status }}</strong>
      <span class="form-help"> · {{ TARGET_KIND[job.target_kind]?.name ?? '服务器' }}「{{ job.server_name }}」<template v-if="job.mode"> · {{ networkName(job.mode) }}</template> · 开始于 {{ formatTime(job.started_at) }}<template v-if="job.finished_at"> · 结束于 {{ formatTime(job.finished_at) }}</template></span>
    </p>
    <ol class="lb-job-steps">
      <li v-for="step in job.steps" :key="text(step.key)" :data-status="text(step.status)">
        <span>{{ DEPLOY_STEP[text(step.key)] ?? text(step.key) }}<template v-if="text(step.detail)">：{{ text(step.detail) }}</template></span>
        <span class="asv-state" :class="{ 'tone-green': step.status === 'done', 'tone-red': step.status === 'failed', 'tone-blue': step.status === 'running', 'tone-muted': step.status === 'pending' }">{{ DEPLOY_STEP_STATUS[text(step.status)] ?? text(step.status) }}</span>
      </li>
    </ol>
    <p v-if="job.status === 'succeeded' && job.kind === 'deploy'" role="status">部署完成，已经登记到工作台。接下来在“平台与账号”里登录它控制的平台。</p>
    <p v-else-if="job.status === 'succeeded' && job.kind === 'redeploy'" role="status">重新部署完成：登录状态和会话记录都保留着。</p>
    <p v-else-if="job.status === 'succeeded' && job.kind === 'restart'" role="status">重启完成：工作台已经重新连上它。</p>
    <p v-else-if="job.status === 'succeeded' && forgotten" role="status" class="lb-warn">只删除了工作台里的登记，没有连服务器：上面写的那些还留在服务器上，需要时到服务器上手动清理。</p>
    <p v-else-if="job.status === 'succeeded'" role="status">卸载完成。</p>
    <p v-else-if="job.status === 'failed'" role="alert" class="lb-warn is-red">{{ job.error_message ?? '失败，详见命令输出' }}<template v-if="job.error_code">（{{ job.error_code }}）</template></p>
    <p v-else-if="job.status === 'interrupted'" role="alert" class="lb-warn">任务被中断（工作台重启过）。可以重新开始。</p>
    <details v-if="job.log" :open="job.status === 'failed'"><summary>命令输出（密码、令牌已替换成 ***）</summary><pre class="lb-job-log">{{ job.log }}</pre></details>
  </section>
</template>
