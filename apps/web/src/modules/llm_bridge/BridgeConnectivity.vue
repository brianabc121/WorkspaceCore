<script setup lang="ts">
import { computed } from 'vue'
import type { Connectivity, ConnectivityTarget } from './api'

// 一个平台账号卡片里的“平台连通性”（T-0240）：这个 MCP 管理器所在的服务器到这个平台的各个地址
// 能不能用、延迟多少。有些服务器访问不了 ChatGPT / Claude，或者连得上但被平台按 IP、地区拒绝——
// 登录之前就在这里说清楚。数据由管理器在它那台服务器上测出来；这里只负责显示。
// account：账号槽位（openai / claude / workbuddy）
const props = defineProps<{ data?: Connectivity; account: string; label: string; loading: boolean; failed: string }>()

const info = computed(() => props.data?.slots[props.account] ?? null)
const STATE: Record<string, { text: string; tone: string }> = {
  ok: { text: '可用', tone: 'tone-green' },
  blocked: { text: '被平台拒绝', tone: 'tone-red' },
  unreachable: { text: '连不上', tone: 'tone-red' },
}
const stateOf = (t: ConnectivityTarget) => STATE[t.state] ?? { text: t.ok ? '可用' : '不可用', tone: t.ok ? 'tone-green' : 'tone-red' }
const latency = (t: ConnectivityTarget) => (t.latency_ms == null ? '' : `${t.latency_ms} ms`)
// 延迟高（超过 1.5 秒）时提醒一句：能用，但会慢
const slow = computed(() => !!info.value?.ok && info.value.targets.some(t => t.ok && (t.latency_ms ?? 0) > 1500))
const blocked = computed(() => !!info.value && info.value.targets.some(t => t.state === 'blocked'))
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
          <span class="lb-net-result"><span class="asv-state" :class="stateOf(t).tone">{{ stateOf(t).text }}</span><span v-if="latency(t)" class="lb-net-ms">{{ latency(t) }}</span></span>
          <span v-if="t.error" class="lb-net-error">{{ t.error }}</span>
        </li>
      </ul>
      <p v-if="!info.ok" role="alert" class="lb-warn is-red">
        这个 MCP 管理器所在的服务器现在用不了{{ label }}：登录和对话都会失败。
        <template v-if="blocked">网络是通的，但平台拒绝了这台服务器（通常是它的 IP 或所在地区不在平台允许的范围里）。</template>
        可以把 MCP 管理器部署到能访问这个平台的服务器上（“添加 MCP 管理器”里选“智能体服务器”或“容器服务器”）。
      </p>
      <p v-else-if="info.need === 'any' && info.targets.some(t => !t.ok)" class="form-help">国内站和国际站有一个可用就行。</p>
      <p v-if="slow" class="form-help">延迟比较高：能用，但登录和回答会慢一些。</p>
    </template>
  </div>
</template>
