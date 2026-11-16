<script setup lang="ts">
import { computed } from 'vue'
import type { Schema } from '../../services/api'
import JobProgress from '../../components/JobProgress.vue'
import { SSH_JOB_KIND, SSH_STEP } from './ssh'

// 一次 SSH 任务（初始化服务器、部署、卸载）的逐步进度与脱敏后的命令输出（T-0090、T-0147；按效果图重排 T-0269）。
// 外观在共用组件 JobProgress（T-0272，MCP 管理器的部署也用它）；这里只把任务记录换成它的参数。
// 步骤与失败原因都来自任务记录本身；没登记文字的新步骤直接显示步骤名，不猜。
const props = defineProps<{ job: Schema['AgentSshJobOut'] }>()
type Step = Schema['AgentSshJobStep']

const steps = computed(() => props.job.steps.map(s => ({ key: s.key, label: (SSH_STEP as Record<string, string>)[s.key] ?? s.key, detail: s.detail, status: s.status })))
// 成功条上的要点（只对初始化列出它准备好的东西）
const READY: Partial<Record<Step['key'], string>> = { docker: 'Docker 与 Compose', workspace: '部署目录', verify: '平台连通' }
const chips = computed(() => props.job.kind === 'prepare' ? props.job.steps.filter(s => s.status === 'done' && READY[s.key]).map(s => READY[s.key]!) : [])
const SUCCESS: Record<Schema['AgentSshJobOut']['kind'], string> = {
  prepare: '服务器初始化完成：可以在这台服务器上部署智能体了。',
  install: '部署完成，实例已连上工作台。',
  uninstall: '卸载完成。',
}
</script>

<template>
  <JobProgress
    :label="`${SSH_JOB_KIND[job.kind]}进度`" :title="SSH_JOB_KIND[job.kind]" :status="job.status"
    :started-at="job.started_at" :finished-at="job.finished_at" :updated-at="job.updated_at"
    :steps="steps" :log="job.log" log-note="密码、令牌、私钥已替换成 ***"
    :error-message="job.error_message" :error-code="job.error_code" :success-text="SUCCESS[job.kind]" :chips="chips"
  />
</template>
