<script setup lang="ts">
import { computed } from 'vue'
import { Cpu, Database, MemoryStick } from '@lucide/vue'
import type { RunnerEnv } from './api'
import { ago, binary, cores, cpuReading, memoryReading, volumeReading, type Reading } from './containerMetrics'

// 详情里的“资源负载”（T-0187）：CPU、内存、数据卷各一块，各自写上报时间与状态。
// 口径写在每块下面：CPU 是最近一个采样窗口的平均核数（不是瞬时值、不是配置的上限）；内存含页缓存；
// 数据卷不含镜像与容器可写层。未知、过期、未运行如实写出来，不补 0。
const props = defineProps<{ env: RunnerEnv }>()
const cpu = computed(() => cpuReading(props.env))
const mem = computed(() => memoryReading(props.env))
const vol = computed(() => volumeReading(props.env))
const window = computed(() => props.env.metrics?.cpu_window_seconds ?? null)
const live = (r: Reading) => r.status === 'ok' || r.status === 'stale' || r.status === 'legacy'
function when(r: Reading) {
  if (r.status === 'ok') return `上报于 ${ago(r.at)}`
  if (r.status === 'stale') return r.at ? `已过期 · 上报于 ${ago(r.at)}` : '已过期 · 上报时间未知'
  if (r.status === 'legacy') return '旧版读数 · 上报时间未知'
  if (r.status === 'not_running') return '环境没在运行'
  return r.note || '未知'
}
</script>

<template>
  <div class="cs-meters">
    <section class="cs-meter" aria-label="CPU">
      <div class="cs-meter-head"><Cpu aria-hidden="true" /><span>CPU 已用 <b>{{ cpu.value }}</b><template v-if="live(cpu)"> 核</template> <span class="cs-limit">/ 上限 {{ cores(env.cpus) }} 核</span></span><span v-if="cpu.percent !== null" class="cs-pct">{{ Math.round(cpu.percent) }}%</span></div>
      <span v-if="cpu.percent !== null" class="cs-bar is-cpu" :class="`is-${cpu.status}`" role="meter" aria-label="CPU 占上限" :aria-valuenow="Math.round(cpu.percent)" aria-valuemin="0" aria-valuemax="100"><span :style="{ width: `${cpu.bar}%` }" /></span>
      <p class="cs-meter-when" :class="`is-${cpu.status}`">{{ when(cpu) }}<template v-if="cpu.status === 'ok' || cpu.status === 'stale'"><template v-if="window"> · 最近 {{ Math.round(window) }} 秒的平均</template></template></p>
      <p v-if="cpu.status === 'unavailable'" class="form-help">刚启动或刚重启的容器要等第二次上报（约 1 分钟）才有读数；旧版 runner-agent 不采集 CPU，升级接入包后就有。</p>
    </section>
    <section class="cs-meter" aria-label="内存">
      <div class="cs-meter-head"><MemoryStick aria-hidden="true" /><span>内存已用 <b>{{ mem.value }}</b><template v-if="live(mem)"> MiB</template> <span class="cs-limit">/ 上限 {{ env.memory_mb }} MiB</span></span><span v-if="mem.percent !== null" class="cs-pct">{{ Math.round(mem.percent) }}%</span></div>
      <span v-if="mem.percent !== null" class="cs-bar is-mem" :class="`is-${mem.status}`" role="meter" aria-label="内存占上限" :aria-valuenow="Math.round(mem.percent)" aria-valuemin="0" aria-valuemax="100"><span :style="{ width: `${mem.bar}%` }" /></span>
      <p class="cs-meter-when" :class="`is-${mem.status}`">{{ when(mem) }}</p>
    </section>
    <section class="cs-meter" aria-label="数据卷">
      <div class="cs-meter-head"><Database aria-hidden="true" /><span>数据卷占用 <b>{{ vol.value }}</b></span></div>
      <p class="cs-meter-when" :class="`is-${vol.status}`">{{ when(vol) }}</p>
      <p v-if="env.snapshot_at" class="form-help">快照：{{ binary(env.snapshot_bytes) }}（另外保存，不算在数据卷里）</p>
    </section>
    <p class="form-help cs-caliber">CPU 是两次上报之间的平均核数，可以超过 1；内存按 Docker 的统计（含页缓存）；数据卷只算环境自己的数据卷，不含镜像与容器可写层。读数每分钟随对账上报一次，超过 3 分钟没有新读数就标为过期。</p>
  </div>
</template>
