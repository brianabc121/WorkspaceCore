<script setup lang="ts">
import { ref, watch } from 'vue'
import { deployError, type DeployJob as Job, type LlmBridgeApi } from './api'
import DeployJob from './DeployJob.vue'

// 一个部署 / 重新部署 / 重启 / 卸载任务的进度卡片（T-0272）：按任务号读进度（进行中每 2 秒一次），
// 结束时把结果交给页面一次（刷新列表、选中部署好的管理器、sudo 密码不对时重新打开确认框）。
// 页面上刚开始的任务和刷新页面后找回来的进行中的任务都用它，所以刷新不会丢进度。
const props = defineProps<{ api: LlmBridgeApi; jobId: string }>()
const emit = defineEmits<{ settled: [job: Job]; close: [] }>()
const job = props.api.useDeployment(ref<string | null>(props.jobId))
// 按“哪个任务 + 状态”只报一次：已经结束的任务（例如只删除登记，返回时就是成功）也报一次
let reported = ''
watch(() => (job.data.value ? `${job.data.value.id}:${job.data.value.status}` : ''), key => {
  const data = job.data.value
  if (!data || data.status === 'running' || key === reported) return
  reported = key
  emit('settled', data)
}, { immediate: true })
</script>

<template>
  <div class="lb-form lb-progress">
    <p v-if="job.isPending.value" role="status" class="form-help">正在读取进度…</p>
    <DeployJob v-else-if="job.data.value" :job="job.data.value" />
    <p v-if="job.error.value" role="alert" class="lb-warn is-red">读不到进度：{{ deployError(job.error.value) }}</p>
    <div v-if="!job.data.value || job.data.value.status !== 'running'" class="lb-actions"><button type="button" class="secondary-button" @click="emit('close')">关闭</button></div>
  </div>
</template>
