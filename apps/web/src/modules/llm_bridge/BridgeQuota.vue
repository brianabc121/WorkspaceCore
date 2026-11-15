<script setup lang="ts">
import { computed, ref } from 'vue'
import { RefreshCw } from '@lucide/vue'
import { bridgeError, platformName, quotaKind, quotaUsage, seconds, type Bridge, type BridgeStatus, type LlmBridgeApi, type QuotaWindow } from './api'

// 每个平台的额度（T-0237）：每个窗口已用多少、什么时候重置；因为额度用完暂停到什么时候、有几个会话在等自动续跑。
// 平台给的是已用百分比和重置时间，不是剩余 token 数；Claude / WorkBuddy 只在跑回合时顺带报出来，
// 所以显示的是“最近一次看到的”。旧版本的管理器没有这些数据，这一块不显示。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge; status: BridgeStatus }>()

const rows = computed(() => Object.entries(props.status.quota ?? {}).map(([platform, quota]) => ({ platform, quota })))
const canRefresh = computed(() => rows.value.some(r => r.quota.can_refresh))
const error = ref(''), busy = ref(false)
async function refresh() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await props.api.refreshQuota(props.bridge.id) } catch (e) { error.value = bridgeError(e) } finally { busy.value = false }
}
const tone = (w: QuotaWindow) => (w.reset_passed ? 'tone-muted' : w.status === 'exceeded' ? 'tone-red' : w.status === 'warning' ? 'tone-amber' : 'tone-green')
const state = (w: QuotaWindow) => (w.reset_passed ? '待确认' : w.status === 'exceeded' ? '已用完' : w.status === 'warning' ? '快用完' : '正常')
const percent = (w: QuotaWindow) => (w.reset_passed || w.used_percent == null ? null : Math.min(100, Math.max(0, Math.round(w.used_percent))))
</script>

<template>
  <section v-if="rows.length" class="lb-quota" aria-label="额度">
    <div class="lb-quota-head">
      <h3>额度</h3>
      <button v-if="canRefresh" type="button" class="secondary-button" :disabled="busy" @click="refresh"><RefreshCw aria-hidden="true" />{{ busy ? '正在查…' : '向平台查一次' }}</button>
    </div>
    <p class="form-help">平台给的是已用百分比和重置时间，不是剩余的 token 数。Codex 可以随时查；Claude 和 WorkBuddy 只在跑回合时顺带报出来，这里显示最近一次看到的。</p>
    <p v-if="error" class="lb-warn is-red" role="alert">{{ error }}</p>
    <div class="lb-accounts">
      <article v-for="r in rows" :key="r.platform" class="lb-account" :aria-label="`额度：${platformName(r.platform)}`">
        <div class="lb-account-head"><h4>{{ platformName(r.platform) }}</h4><span v-if="r.quota.paused_until" class="asv-state tone-red">已暂停</span></div>
        <p v-if="r.quota.paused_until" role="note" class="lb-warn is-red">
          额度用完，这个平台暂停到 {{ seconds(r.quota.paused_until) }}{{ r.quota.pause_estimated ? '（平台没有给出重置时间，这是估计的）' : '' }}；到时间后自动恢复。
          <template v-if="r.quota.waiting_resume">有 {{ r.quota.waiting_resume }} 个被打断的会话在等自动继续。</template>
        </p>
        <p v-if="!r.quota.windows.length" class="form-help">还没有看到这个平台的额度{{ r.quota.can_refresh ? '：点“向平台查一次”' : '：跑过一个回合后会显示' }}。</p>
        <ul v-else class="lb-windows">
          <li v-for="w in r.quota.windows" :key="w.kind">
            <div class="lb-window-head"><strong>{{ quotaKind(w.kind) }}</strong><span class="asv-state" :class="tone(w)">{{ state(w) }}</span></div>
            <progress v-if="percent(w) !== null" max="100" :value="percent(w)!" :aria-label="`${quotaKind(w.kind)}已用`" />
            <dl class="lb-meta">
              <dt>用量</dt><dd>{{ quotaUsage(w) }}</dd>
              <dt>重置时间</dt><dd>{{ w.resets_at ? seconds(w.resets_at) : '平台没有给出' }}</dd>
              <template v-if="w.plan"><dt>套餐</dt><dd>{{ w.plan }}</dd></template>
              <dt>看到的时间</dt><dd>{{ seconds(w.observed_at) }}</dd>
            </dl>
          </li>
        </ul>
        <p class="form-help">最近 5 小时经这个管理器用掉 {{ r.quota.tokens_5h.toLocaleString() }} 个 token（管理器自己记的，不含别处用的）。</p>
      </article>
    </div>
  </section>
</template>
