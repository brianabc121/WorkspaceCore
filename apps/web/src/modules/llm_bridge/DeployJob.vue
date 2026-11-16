<script setup lang="ts">
import { computed } from 'vue'
import JobProgress from '../../components/JobProgress.vue'
import { DEPLOY_KIND, DEPLOY_STEP, TARGET_KIND, networkName, type DeployJob } from './api'

// 一次部署 / 重新部署 / 重启 / 卸载任务的逐步进度和脱敏后的命令输出。步骤和失败原因都来自任务记录本身。
// 外观和智能体服务器的初始化一样（共用组件 JobProgress，T-0272）：标题 + 状态 + 用时、分格进度条、步骤时间线、
// 失败原因挂在失败的那一步下面、进行中那一步下面的最新输出、折叠的命令输出；成功后收起成一条成功条。
const props = defineProps<{ job: DeployJob }>()
const text = (value: unknown) => (typeof value === 'string' ? value : '')
const kind = computed(() => DEPLOY_KIND[props.job.kind] ?? props.job.kind)
const steps = computed(() => props.job.steps.map((s, i) => {
  const key = text(s.key) || String(i)
  return { key, label: DEPLOY_STEP[key] ?? key, detail: text(s.detail) || null, status: text(s.status) }
}))
const meta = computed(() => [`${TARGET_KIND[props.job.target_kind]?.name ?? '服务器'}「${props.job.server_name}」`, props.job.mode ? networkName(props.job.mode) : ''].filter(Boolean).join(' · '))
// “只删除登记”的卸载：没有连服务器，只有“从工作台去掉登记”一步，说明里写着服务器上还留着什么，所以那一步一直显示
const forgotten = computed(() => props.job.kind === 'uninstall' && props.job.steps.length === 1 && props.job.steps[0]?.key === 'unregister')
const SUCCESS: Record<string, string> = {
  deploy: '部署完成，已经登记到工作台。接下来在“平台与账号”里登录它控制的平台。',
  redeploy: '重新部署完成：登录状态和会话记录都保留着。',
  restart: '重启完成：工作台已经重新连上它。',
  uninstall: '卸载完成。',
}
const success = computed(() => forgotten.value ? '已从工作台去掉登记。' : SUCCESS[props.job.kind] ?? '完成。')
</script>

<template>
  <JobProgress
    :label="`${kind}「${job.name}」的进度`" :title="`${kind}「${job.name}」`" :status="job.status" :meta="meta"
    :started-at="job.started_at" :finished-at="job.finished_at"
    :steps="steps" :log="job.log" log-note="密码、令牌已替换成 ***"
    :error-message="job.error_message" :error-code="job.error_code" :success-text="success" :collapse="!forgotten"
  >
    <template v-if="forgotten && job.status === 'succeeded'" #after>
      <p role="note" class="lb-warn">只删除了工作台里的登记，没有连服务器：上面写的那些还留在服务器上，需要时到服务器上手动清理。</p>
    </template>
  </JobProgress>
</template>
