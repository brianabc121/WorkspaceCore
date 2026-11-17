<script setup lang="ts">
import { computed } from 'vue'
import { DNS_SLOW_MS, type Connectivity, type ConnectivityTarget } from './api'

// 一个平台账号卡片里的“平台连通性”（T-0240）：这个 MCP 管理器所在的服务器到这个平台的各个地址
// 能不能用、延迟多少。有些服务器访问不了 ChatGPT / Claude，或者连得上但被平台按 IP、地区拒绝——
// 登录之前就在这里说清楚。数据由管理器在它那台服务器上测出来；这里只负责显示。
// 延迟（T-0278）：建立连接用的时间，和 ping 可比；每一步（解析 / 连接 / 加密握手 / 平台应答）分开列出，
// 慢的那一步标出来。旧版本的管理器只给一次完整访问的总耗时，照样显示并说明。
// account：账号槽位（openai / claude / workbuddy）
const props = defineProps<{ data?: Connectivity; account: string; label: string; loading: boolean; failed: string }>()

const info = computed(() => props.data?.slots[props.account] ?? null)
const targets = computed(() => info.value?.targets ?? [])
const STATE: Record<string, { text: string; tone: string }> = {
  ok: { text: '可用', tone: 'tone-green' },
  blocked: { text: '被平台拒绝', tone: 'tone-red' },
  unreachable: { text: '连不上', tone: 'tone-red' },
}
const stateOf = (t: ConnectivityTarget) => STATE[t.state] ?? { text: t.ok ? '可用' : '不可用', tone: t.ok ? 'tone-green' : 'tone-red' }
const latency = (t: ConnectivityTarget) => (t.latency_ms == null ? '' : `${t.latency_ms} ms`)
const seconds = (ms: number) => (ms / 1000).toFixed(1)

type Step = 'dns_ms' | 'connect_ms' | 'tls_ms' | 'response_ms'
const STEPS: { key: Step; name: string; stage: string; slow: number }[] = [
  { key: 'dns_ms', name: '解析', stage: 'dns', slow: DNS_SLOW_MS },
  { key: 'connect_ms', name: '连接', stage: 'connect', slow: 1500 },
  { key: 'tls_ms', name: '加密握手', stage: 'tls', slow: 1500 },
  { key: 'response_ms', name: '平台应答', stage: 'response', slow: 3000 },
]
// 一个地址每一步的用时：做完的给毫秒数，卡住的那一步写“没有完成”，慢的标出来
function steps(t: ConnectivityTarget) {
  const timings = t.timings
  if (!timings) return []
  const out: { key: string; text: string; slow: boolean }[] = []
  for (const step of STEPS) {
    const ms = timings[step.key]
    if (ms != null) out.push({ key: step.key, text: `${step.name} ${ms} ms`, slow: ms >= step.slow })
    else if (t.stage === step.stage) out.push({ key: step.key, text: `${step.name}没有完成`, slow: true })
  }
  if (t.via_proxy) out.push({ key: 'proxy', text: '经 HTTPS 代理（域名由代理解析）', slow: false })
  else if (t.address) out.push({ key: 'address', text: `IP ${t.address}`, slow: false })
  return out
}
const slowest = (key: Step) => Math.max(0, ...targets.value.map(t => t.timings?.[key] ?? 0))
const timed = computed(() => targets.value.some(t => !!t.timings))
// 旧版本的管理器：毫秒数是完整访问一次的总耗时
const legacy = computed(() => targets.value.some(t => t.latency_ms != null && !t.timings))
const dnsSlow = computed(() => slowest('dns_ms'))
const dnsStuck = computed(() => targets.value.some(t => t.stage === 'dns' && !!t.error?.includes('超时')))
const dnsTrouble = computed(() => dnsSlow.value >= DNS_SLOW_MS || dnsStuck.value)
const responseSlow = computed(() => slowest('response_ms'))
const connectSlow = computed(() => slowest('connect_ms'))
// 延迟（建立连接）高，或旧版本的总耗时高：能用，但会慢
const slow = computed(() => !!info.value?.ok && targets.value.some(t => t.ok && (t.latency_ms ?? 0) > 1500))
const blocked = computed(() => targets.value.some(t => t.state === 'blocked'))
</script>

<template>
  <div class="lb-net" role="group" :aria-label="`平台连通性：${label}`">
    <div class="lb-net-head">
      <strong>平台连通性</strong>
      <span v-if="info" class="asv-state" :class="info.ok ? 'tone-green' : 'tone-red'">{{ info.ok ? '可用' : '这台服务器用不了' }}</span>
    </div>
    <p v-if="loading && !info" role="status" class="form-help">正在检测这台服务器能不能访问这个平台…</p>
    <p v-else-if="failed && !info" class="form-help">检测没有成功：{{ failed }}</p>
    <p v-else-if="data && !data.supported" class="form-help">这个 MCP 管理器的版本还不支持检测：升级（重新部署）后就有。</p>
    <template v-else-if="info">
      <ul class="lb-net-list">
        <li v-for="t in info.targets" :key="t.host">
          <span class="lb-net-host"><span>{{ t.label }}</span><code>{{ t.host }}</code></span>
          <span class="lb-net-result"><span class="asv-state" :class="stateOf(t).tone">{{ stateOf(t).text }}</span><span v-if="latency(t)" class="lb-net-ms" :title="t.timings ? '延迟：建立连接用的时间，和 ping 可比' : '旧版本的管理器：完整访问一次的总耗时'">{{ latency(t) }}</span></span>
          <span v-if="steps(t).length" class="lb-net-steps" :aria-label="`每一步的用时：${t.host}`">
            <span v-for="s in steps(t)" :key="s.key" :class="{ 'is-slow': s.slow }">{{ s.text }}</span>
          </span>
          <span v-if="t.error" class="lb-net-error">{{ t.error }}</span>
        </li>
      </ul>
      <p v-if="!info.ok" role="alert" class="lb-warn is-red">
        这个 MCP 管理器所在的服务器现在用不了{{ label }}：登录和对话都会失败。
        <template v-if="blocked">网络是通的，但平台拒绝了这台服务器（通常是它的 IP 或所在地区不在平台允许的范围里）。</template>
        <template v-if="dnsTrouble && !blocked">卡在域名解析上（见下面），不一定是这台服务器访问不了平台。</template>
        <template v-else>可以把 MCP 管理器部署到能访问这个平台的服务器上（“添加 MCP 管理器”里选“智能体服务器”或“容器服务器”）。</template>
      </p>
      <p v-else-if="info.need === 'any' && info.targets.some(t => !t.ok)" class="form-help">国内站和国际站有一个可用就行。</p>
      <p v-if="dnsTrouble" class="lb-hint-amber">
        域名解析慢<template v-if="dnsSlow >= DNS_SLOW_MS">（最慢 {{ seconds(dnsSlow) }} 秒）</template>：MCP 管理器的容器每查一次域名都要等好几秒，
        多半是它用的某个 DNS 服务器不应答，要等超时才换下一个。官方程序每次连平台也要多等这么久，登录可能因此超时。
        <template v-if="timed && connectSlow < 1500">建立连接最多只用了 {{ connectSlow }} ms，网络本身不慢。</template>
      </p>
      <p v-if="responseSlow >= 3000" class="form-help">平台应答慢（最慢 {{ seconds(responseSlow) }} 秒）：网络本身没问题，是平台那边回得慢。</p>
      <p v-if="slow" class="form-help">延迟比较高：能用，但登录和回答会慢一些。</p>
      <p v-if="timed" class="form-help">延迟是建立连接用的时间（一次网络往返），可以和服务器上 ping 的结果对照。</p>
      <p v-if="legacy" class="form-help">这个 MCP 管理器是旧版本：毫秒数是完整访问一次的总耗时（含域名解析和平台应答），不是网络延迟。重新部署（升级）后会分开显示每一步。</p>
    </template>
  </div>
</template>
