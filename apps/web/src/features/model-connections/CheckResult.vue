<script setup lang="ts">
import { computed } from 'vue'
import { CircleAlert, CircleCheck, Clock, Info } from '@lucide/vue'
import { formatTime } from '../time'
import { checkStatus, isStale, type ConnectionCheck } from './api'

// 检测结果卡（契约第 1 节“检测结果”、规范第 4.4 节）：元数据检测只证明认证与模型列表接口可用，推理检测才是真实请求；
// 429 显示“服务商限流中”与可重试时间（没有合法值就说稍后重试，不编时间）；401 / 403 提示更换 Key；
// 不支持列模型显示“需手动配置模型”；超过有效期（默认 15 分钟）写明检测时间与“结果可能已过期”，不当实时健康。
const props = defineProps<{ check: ConnectionCheck | null; freshSeconds?: number; now?: number }>()
const status = computed(() => checkStatus(props.check))
const stale = computed(() => !!props.check && isStale(props.check, props.freshSeconds, props.now))
const credential = computed(() => props.check?.reason === 'unauthorized' || props.check?.reason === 'forbidden' || props.check?.reason === 'credential_unreadable')
</script>

<template>
  <section class="mc-check" :class="`tone-${status.tone}`" aria-label="检测结果">
    <p class="mc-check-title">
      <CircleCheck v-if="status.tone === 'green'" aria-hidden="true" /><CircleAlert v-else-if="status.tone !== 'muted'" aria-hidden="true" /><Info v-else aria-hidden="true" />
      <strong>{{ status.label }}</strong>
      <span v-if="check" class="mc-check-mode">{{ check.mode === 'inference' ? '推理检测' : '元数据检测' }}</span>
    </p>
    <p class="mc-check-detail">{{ status.detail }}</p>
    <p v-if="check?.status === 'rate_limited'" class="mc-check-detail">
      <template v-if="check.retry_at">可在 <time :datetime="check.retry_at">{{ formatTime(check.retry_at) }}</time> 之后重试。</template>
      <template v-else>服务商没有给出可重试时间，请稍后重试。</template>
    </p>
    <p v-if="credential" class="mc-check-detail">请用“更换”填写新的 API Key 后重新检测。</p>
    <p v-if="check" class="mc-check-meta">
      <Clock aria-hidden="true" />检测于 <time :datetime="check.checked_at">{{ formatTime(check.checked_at) }}</time>
      <template v-if="check.latency_ms !== null"> · {{ check.latency_ms }} ms</template>
      <template v-if="check.mode === 'inference' && check.model_id"> · 模型 <code>{{ check.model_id }}</code></template>
      <strong v-if="stale" class="mc-stale"> · 结果可能已过期</strong>
    </p>
  </section>
</template>
