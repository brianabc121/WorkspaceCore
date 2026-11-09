<script setup lang="ts">
import { computed } from 'vue'
import { Activity, ArrowRight, Bot, CalendarCheck, Cloud, Database, FileText, Hash, Info, Layers, Newspaper, Play, Plug, Send, Sparkles } from '@lucide/vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import { formatTime } from '../../features/time'
import type { Schema } from '../../services/api'
import { channelName, type NewsSettings, type NewsStatus } from './api'
import { INSTANCE_STATUS, MODE_NAMES, configFile, connectivity, savedChannels, type Feedback, type Tone } from './settingsState'

// 设置 → 新闻早报的右侧一栏（T-0195，效果图 06）：
// - “当前运行状态”只说接口给的事实：是否部署、连通（在线 / 连不上 / 未知 / 不检查）、被别的工作空间占用、
//   配置文件写入（#config_rev 或写入失败）、设置版本（version，与 config_rev 不同）、已保存的方式与执行实例；
// - “推送渠道”列出已保存且启用的渠道（已配置 ≠ 已送达），以及 owner 才有的“发送测试推送”“现在生成一份”。
//   有未保存更改时按钮变成“保存并…”，由设置页先保存再执行；测试结果按渠道原样列出。
const props = defineProps<{
  settings: NewsSettings; status: NewsStatus | undefined; statusFailed: boolean
  instances: Schema['AssigneeInstance'][] | undefined; instancesFailed: boolean
  canEdit: boolean; dirty: boolean; busy: '' | 'save' | 'test' | 'generate'; canGenerate: boolean
  results: Schema['NewsPushResult'][] | null; feedback: Feedback | null
}>()
const emit = defineEmits<{ test: []; generate: []; channels: [] }>()

const link = computed(() => connectivity(props.settings, props.status, props.statusFailed))
const file = computed(() => configFile(props.settings))
const channels = computed(() => savedChannels(props.settings))
const instance = computed<{ tone: Tone; label: string }>(() => {
  const id = props.settings.agent_instance_id
  if (!id) return { tone: 'red', label: '还没有选择' }
  const found = props.instances?.find(i => i.id === id)
  if (found) return { tone: found.status === 'online' ? 'green' : 'amber', label: `${found.name}（${INSTANCE_STATUS[found.status] ?? found.status}）` }
  if (props.instances) return { tone: 'red', label: '已停用或已删除的实例' }
  return { tone: 'muted', label: props.instancesFailed ? '实例列表读取失败' : '正在读取…' }
})
const dataDate = computed(() => props.status?.latest_data_date ?? (props.status?.online === true ? '还没有' : '未知'))
</script>

<template>
  <ManagementSection title="当前运行状态">
    <template #title><span class="nws-title"><Activity aria-hidden="true" />当前运行状态</span></template>
    <dl class="nws-facts">
      <div><dt><Cloud aria-hidden="true" />TrendRadar</dt><dd><span class="asv-state" :class="settings.deployed ? 'tone-green' : 'tone-muted'">{{ settings.deployed ? '已部署' : '未部署' }}</span></dd></div>
      <div><dt><Plug aria-hidden="true" />连通</dt><dd><span class="asv-state" :class="`tone-${link.tone}`">{{ link.label }}</span></dd></div>
      <div v-if="settings.taken_by_other"><dt><Layers aria-hidden="true" />占用</dt><dd><span class="asv-state tone-red">另一个工作空间在用</span></dd></div>
      <div><dt><CalendarCheck aria-hidden="true" />早报开关</dt><dd>{{ settings.enabled ? '已启用' : '已关闭' }}</dd></div>
      <div class="nws-fact-wide">
        <dt><FileText aria-hidden="true" />配置文件</dt>
        <dd>
          <span class="asv-state" :class="`tone-${file.tone}`">{{ file.label }}</span>
          <span v-if="settings.rendered_at" class="asv-sub">最近写成功：{{ formatTime(settings.rendered_at) }}</span>
          <span v-if="settings.render_error" class="nws-error-text">{{ settings.render_error }}</span>
        </dd>
      </div>
      <div><dt><Hash aria-hidden="true" />设置版本</dt><dd>第 {{ settings.version }} 版</dd></div>
      <div><dt><Sparkles aria-hidden="true" />当前方式</dt><dd>{{ MODE_NAMES[settings.briefing_mode] }}</dd></div>
      <div v-if="settings.briefing_mode === 'agent'"><dt><Bot aria-hidden="true" />执行实例</dt><dd><span class="asv-state" :class="`tone-${instance.tone}`">{{ instance.label }}</span></dd></div>
      <div><dt><Database aria-hidden="true" />最近抓到数据</dt><dd>{{ status ? dataDate : '—' }}</dd></div>
      <div><dt><Newspaper aria-hidden="true" />最近一份早报</dt><dd>{{ status ? formatTime(status.latest_briefing_at) : '—' }}</dd></div>
    </dl>
    <p v-if="status?.error" class="nws-error-text">TrendRadar 的说明：{{ status.error }}</p>
    <p class="mg-hint"><Info aria-hidden="true" /><span>“设置版本”每保存一次加一，只用来防止覆盖别人的修改；“#{{ settings.config_rev }}”是配置文件写入的次数。写入不代表 TrendRadar 已按新配置运行：它在下一次运行时才读取。</span></p>
  </ManagementSection>

  <ManagementSection title="推送渠道">
    <template #title><span class="nws-title"><Send aria-hidden="true" />推送渠道</span></template>
    <ul v-if="channels.enabled.length" class="nws-channel-list" aria-label="已启用的推送渠道">
      <li v-for="c in channels.enabled" :key="c.id">
        <Send aria-hidden="true" />
        <div><strong>{{ c.name }}</strong><span class="asv-state" :class="`tone-${c.state.tone}`">{{ c.state.label }}</span></div>
      </li>
    </ul>
    <p v-else class="asv-sub">还没有启用推送渠道：早报只推到工作台（早报页与站内通知）。</p>
    <p v-if="channels.enabled.length && channels.disabled" class="asv-sub">另有 {{ channels.disabled }} 个渠道未启用。</p>
    <button type="button" class="asv-link" @click="emit('channels')">管理全部渠道<ArrowRight aria-hidden="true" /></button>
    <p class="mg-hint"><Info aria-hidden="true" /><span>测试推送和现在生成只用已保存的设置；“已配置”不代表一定送达，请以渠道里实际收到为准。</span></p>
    <template v-if="canEdit">
      <div class="nws-side-actions">
        <button type="button" class="primary-button" :disabled="!!busy" @click="emit('test')"><Send aria-hidden="true" />{{ busy === 'test' ? '正在发送…' : dirty ? '保存并发送测试推送' : '发送测试推送' }}</button>
        <button type="button" class="secondary-button" :disabled="!!busy || !canGenerate" aria-describedby="nws-generate-help" @click="emit('generate')"><Play aria-hidden="true" />{{ busy === 'generate' ? '正在开始…' : dirty ? '保存并现在生成' : '现在生成一份' }}</button>
      </div>
      <p id="nws-generate-help" class="form-help">{{ canGenerate ? '让执行智能体现在写一份早报，写好后按推送渠道推送。' : '只有“智能体写早报”方式能现在生成一份。' }}</p>
    </template>
    <p v-else class="asv-sub">只有 owner 能发送测试推送或现在生成早报。</p>
    <p v-if="feedback" :role="feedback.tone === 'bad' ? 'alert' : 'status'" class="nws-inline" :class="feedback.tone === 'good' ? 'tone-green' : feedback.tone === 'warn' ? 'tone-amber' : 'tone-red'">{{ feedback.text }}</p>
    <ul v-if="results?.length" class="nws-results" aria-label="测试推送结果">
      <li v-for="r in results" :key="r.channel" :class="r.ok ? 'is-ok' : 'is-failed'">{{ channelName(r.channel) }}：{{ r.ok ? '成功' : '失败' }}{{ r.detail ? `（${r.detail}）` : '' }}</li>
    </ul>
  </ManagementSection>
</template>
