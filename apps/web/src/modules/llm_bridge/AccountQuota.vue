<script setup lang="ts">
import { computed } from 'vue'
import { Box } from '@lucide/vue'
import { NO_QUOTA, platformName, quotaKind, quotaUsage, recentUse, resetText, seconds, type Quota, type QuotaWindow } from './api'

// 一个平台的额度（T-0237），放在账号卡片的“使用额度”里（T-____ 按效果图重排）：
// 每个窗口一行名字和已用比例，下面是重置时间和进度条；因为额度用完暂停到什么时候、有几个会话在等自动续跑。
// 平台给的是已用百分比和重置时间，不是剩余 token 数；Claude 只在跑回合时顺带报出来，显示的是“最近一次看到的”；
// CodeBuddy 的官方程序不给额度，照实说明。recent：是否在这里写最近 5 小时的用量（账号卡片把它放在卡片底部）。
const props = withDefaults(defineProps<{ platform: string; quota: Quota; recent?: boolean }>(), { recent: true })
const name = computed(() => platformName(props.platform))
const tone = (w: QuotaWindow) => (w.reset_passed ? 'tone-muted' : w.status === 'exceeded' ? 'tone-red' : w.status === 'warning' ? 'tone-amber' : 'tone-green')
const percent = (w: QuotaWindow) => (w.reset_passed || w.used_percent == null ? null : Math.min(100, Math.max(0, Math.round(w.used_percent))))
// 右上角：已用多少（没有数字时写状态）
const used = (w: QuotaWindow) => (percent(w) !== null ? `已用 ${percent(w)}%` : w.reset_passed ? '待确认' : w.status === 'exceeded' ? '已用完' : '—')
</script>

<template>
  <article class="lb-quota-block" :aria-label="`额度：${name}`">
    <div v-if="quota.paused_until" class="lb-quota-title"><span class="asv-state tone-red">已暂停</span></div>
    <p v-if="quota.paused_until" role="note" class="lb-warn is-red">
      额度用完，这个平台暂停到 {{ seconds(quota.paused_until) }}{{ quota.pause_estimated ? '（平台没有给出重置时间，这是估计的）' : '' }}；到时间后自动恢复。
      <template v-if="quota.waiting_resume">有 {{ quota.waiting_resume }} 个被打断的会话在等自动继续。</template>
    </p>
    <div v-if="!quota.windows.length && quota.reports_quota === false" class="lb-quota-empty">
      <Box aria-hidden="true" />
      <strong>额度由平台官网提供</strong>
      <p>{{ NO_QUOTA }}</p>
    </div>
    <p v-else-if="!quota.windows.length" class="lb-quota-none">暂无数据：{{ quota.can_refresh ? '点“…”里的“向平台查一次”' : '跑过一个回合后会显示' }}。</p>
    <ul v-else class="lb-windows">
      <li v-for="w in quota.windows" :key="w.kind" :title="`${quotaUsage(w)}；看到于 ${seconds(w.observed_at)}`">
        <div class="lb-window-head"><strong>{{ quotaKind(w.kind) }}</strong><span class="lb-window-used" :class="tone(w)">{{ used(w) }}</span></div>
        <div class="lb-window-row">
          <span class="lb-window-reset">{{ w.reset_passed ? quotaUsage(w) : resetText(w.resets_at) }}<template v-if="w.plan"> · 套餐 {{ w.plan }}</template></span>
          <progress v-if="percent(w) !== null" max="100" :value="percent(w)!" :class="tone(w)" :aria-label="`${quotaKind(w.kind)}已用`" />
        </div>
        <span v-if="!w.reset_passed" class="lb-sr">{{ quotaUsage(w) }}</span>
      </li>
    </ul>
    <p v-if="recent" class="lb-quota-recent">{{ recentUse(quota) }}</p>
  </article>
</template>
