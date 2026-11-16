<script setup lang="ts">
import { computed } from 'vue'
import { NO_QUOTA, platformName, quotaKind, quotaUsage, recentUse, seconds, type Quota, type QuotaWindow } from './api'

// 一个平台的额度（T-0237），放在账号卡片里：每个窗口已用多少（进度条）、什么时候重置；
// 因为额度用完暂停到什么时候、有几个会话在等自动续跑。平台给的是已用百分比和重置时间，不是剩余 token 数；
// Claude 只在跑回合时顺带报出来，显示的是“最近一次看到的”；CodeBuddy 的官方程序不给额度，照实说明。
const props = defineProps<{ platform: string; quota: Quota }>()
const name = computed(() => platformName(props.platform))
const tone = (w: QuotaWindow) => (w.reset_passed ? 'tone-muted' : w.status === 'exceeded' ? 'tone-red' : w.status === 'warning' ? 'tone-amber' : 'tone-green')
const state = (w: QuotaWindow) => (w.reset_passed ? '待确认' : w.status === 'exceeded' ? '已用完' : w.status === 'warning' ? '快用完' : '正常')
const percent = (w: QuotaWindow) => (w.reset_passed || w.used_percent == null ? null : Math.min(100, Math.max(0, Math.round(w.used_percent))))
</script>

<template>
  <article class="lb-quota-block" :aria-label="`额度：${name}`">
    <div class="lb-quota-title"><h4>{{ name }}额度</h4><span v-if="quota.paused_until" class="asv-state tone-red">已暂停</span></div>
    <p v-if="quota.paused_until" role="note" class="lb-warn is-red">
      额度用完，这个平台暂停到 {{ seconds(quota.paused_until) }}{{ quota.pause_estimated ? '（平台没有给出重置时间，这是估计的）' : '' }}；到时间后自动恢复。
      <template v-if="quota.waiting_resume">有 {{ quota.waiting_resume }} 个被打断的会话在等自动继续。</template>
    </p>
    <p v-if="!quota.windows.length && quota.reports_quota === false" class="lb-quota-none">{{ NO_QUOTA }}</p>
    <p v-else-if="!quota.windows.length" class="lb-quota-none">暂无数据：{{ quota.can_refresh ? '点“向平台查一次”' : '跑过一个回合后会显示' }}。</p>
    <ul v-else class="lb-windows">
      <li v-for="w in quota.windows" :key="w.kind">
        <div class="lb-window-head"><strong>{{ quotaKind(w.kind) }}</strong><span class="asv-state" :class="tone(w)">{{ state(w) }}</span></div>
        <progress v-if="percent(w) !== null" max="100" :value="percent(w)!" :class="tone(w)" :aria-label="`${quotaKind(w.kind)}已用`" />
        <dl class="lb-window-meta">
          <dt>用量</dt><dd>{{ quotaUsage(w) }}</dd>
          <dt>重置</dt><dd>{{ w.resets_at ? seconds(w.resets_at) : '平台没有给出' }}</dd>
          <template v-if="w.plan"><dt>套餐</dt><dd>{{ w.plan }}</dd></template>
          <dt>看到</dt><dd>{{ seconds(w.observed_at) }}</dd>
        </dl>
      </li>
    </ul>
    <p class="lb-quota-recent">{{ recentUse(quota) }}</p>
  </article>
</template>
