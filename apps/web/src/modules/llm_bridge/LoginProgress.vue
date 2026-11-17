<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import JobProgress from '../../components/JobProgress.vue'
import type { LoginMethod, LoginState, Slot } from './api'

// 网页登录（CodeBuddy，T-0276）和设备码登录（ChatGPT / Codex，T-0279）的逐步进度：
// 外观和智能体服务器的初始化一样（共用组件 JobProgress）。步骤来自 MCP 管理器报告的进度——
// 网页登录：启动登录服务、申请登录网址、等你登录、确认结果；设备码登录：启动 Codex 的本机程序、
// 向 OpenAI 申请设备码、等你输入验证码并登录、确认结果。第一步“交给 MCP 管理器”由页面自己判断。
// 旧版本的管理器不报告每一步：按页面能知道的情况显示，并说明升级（重新部署）后就能看到每一步。
export interface LoginJob {
  slot: Slot
  label: string
  method: LoginMethod
  /** 点按钮的时间（页面的时钟） */
  startedAt: string
  /** “开始登录”的请求还没回来 */
  requesting: boolean
  /** 管理器已经接手：“开始登录”有了答复，或者查进度看到了这一次的进度 */
  answered: boolean
  /** “开始登录”没等到答复（连接中断、超时），正在向管理器查进度 */
  recovering: boolean
  status: 'running' | 'succeeded' | 'failed'
  url: string
  /** 设备码登录的验证码（网页登录没有） */
  code: string
  /** 管理器报告的这一次的进度（新版本的管理器才有 steps / log） */
  progress: LoginState | null
  error: string
  finishedAt: string | null
}
const props = defineProps<{ job: LoginJob; bridgeName: string }>()
const emit = defineEmits<{ cancel: []; retry: []; close: [] }>()

const SITE: Partial<Record<LoginMethod, string>> = { web_cn: '国内站', web_intl: '国际站' }
const site = computed(() => SITE[props.job.method] ?? '')
const device = computed(() => props.job.method === 'device_code')
// 申请网址 / 设备码那一步的 key（旧版本的管理器不报告步骤时，页面自己拼出这几步）
const ask = computed(() => (device.value ? 'request_code' : 'request_url'))
const LABEL = computed((): Record<string, string> => (device.value
  ? {
      start: '启动 Codex 的本机程序（codex app-server）',
      request_code: '向 OpenAI 申请设备码',
      wait_user: '等你在自己的浏览器里打开网址、输入验证码并登录',
      finish: '确认登录结果',
    }
  : {
      start: `启动 ${props.job.label} 的登录服务`,
      request_url: `向 ${props.job.label}${site.value ? `（${site.value}）` : ''}申请登录网址`,
      wait_user: '等你在自己的浏览器里打开网址并登录',
      finish: '确认登录结果',
    }))
const reported = computed(() => props.job.progress?.steps ?? [])
const legacy = computed(() => props.job.answered && !reported.value.length)

const steps = computed(() => {
  const job = props.job, failed = job.status === 'failed', done = job.status === 'succeeded'
  const connect = {
    key: 'connect', label: `把请求交给 MCP 管理器「${props.bridgeName}」`,
    status: job.answered ? 'done' : failed ? 'failed' : 'running',
    detail: job.answered ? null : job.recovering ? '“开始登录”的请求没等到答复，正在向管理器查进度' : null,
  }
  // 失败那一步的说明就是失败原因时不重复显示（原因挂在那一步下面）
  const reason = job.error || job.progress?.detail || ''
  if (reported.value.length) return [connect, ...reported.value.map(s => ({ key: s.key, label: LABEL.value[s.key] ?? s.key, detail: s.status === 'failed' && s.detail === reason ? null : s.detail || null, status: s.status }))]
  // 管理器还没报告进度（还没接手，或者是旧版本）：按页面知道的情况显示
  const later = (key: string) => ({ key, label: LABEL.value[key]!, detail: null as string | null, status: 'pending' })
  const start = { ...later('start'), status: !job.answered ? 'pending' : job.url || done ? 'done' : failed ? 'failed' : 'running' }
  const request = { ...later(ask.value), status: job.url || done ? 'done' : 'pending' }
  const wait = { ...later('wait_user'), status: done ? 'done' : job.url ? (failed ? 'failed' : 'running') : 'pending' }
  const finish = { ...later('finish'), status: done ? 'done' : 'pending' }
  if (legacy.value && !job.url && !done) start.detail = device.value ? '这个 MCP 管理器的版本不报告每一步：启动本机程序、申请设备码都在这一步里' : '这个 MCP 管理器的版本不报告每一步：启动登录服务、申请网址都在这一步里'
  return [connect, start, request, wait, finish]
})
const log = computed(() => (props.job.progress?.log ?? []).join('\n') || null)
const meta = computed(() => `MCP 管理器「${props.bridgeName}」 · ${device.value ? '设备码登录' : `网页登录${site.value ? `（${site.value}）` : ''}`}`)
const logNote = computed(() => (device.value ? '看起来像密钥或令牌的内容已替换成 ***；验证码只显示在上面，不记在这里' : '访问口令、看起来像密钥或令牌的内容已替换成 ***'))

// 等了一会儿还没有管理器的进度：多半是旧版本的管理器，说明怎么才能看到每一步
const now = ref(Date.now())
const tick = setInterval(() => { now.value = Date.now() }, 1000)
onBeforeUnmount(() => clearInterval(tick))
const quiet = computed(() => props.job.status === 'running' && !reported.value.length && now.value - Date.parse(props.job.startedAt) > 8000)
</script>

<template>
  <JobProgress
    :label="`登录「${job.label}」的进度`" :title="`登录「${job.label}」`" :status="job.status" :meta="meta"
    :started-at="job.startedAt" :finished-at="job.finishedAt" :updated-at="job.finishedAt"
    :steps="steps" :log="log" :log-note="logNote"
    :error-message="job.error || job.progress?.detail || null" :success-text="`「${job.label}」已登录。`"
  >
    <template #after>
      <div v-if="job.status === 'running' && job.url" class="lb-login-open" role="group" :aria-label="device ? '打开网址并输入验证码' : '打开登录网址'">
        <p>{{ device ? '在你自己的浏览器里打开下面的网址，输入验证码并登录，完成后这里会自动更新：' : '在你自己的浏览器里打开下面的网址并登录，完成后这里会自动更新：' }}</p>
        <p><a v-if="job.url.startsWith('https://')" class="asv-link lb-login-url" :href="job.url" target="_blank" rel="noopener noreferrer">{{ job.url }}</a><span v-else class="asv-code">{{ job.url }}</span></p>
        <p v-if="device && job.code">验证码：<strong class="lb-login-code">{{ job.code }}</strong></p>
      </div>
      <p v-if="quiet" role="note" class="form-help">还没收到 MCP 管理器的进度。旧版本的管理器不报告每一步：页面上出现“工作台带的版本比它新”时，重新部署（升级）它就能看到；登录本身照常进行。</p>
      <p v-else-if="legacy && job.status === 'running'" role="note" class="form-help">这个 MCP 管理器的版本不报告每一步，重新部署（升级）后就能看到。</p>
      <div class="lb-actions">
        <button v-if="job.status === 'running'" type="button" class="secondary-button" @click="emit('cancel')">不等了</button>
        <template v-else>
          <button v-if="job.status === 'failed'" type="button" class="primary-button" @click="emit('retry')">{{ device ? '重新获取验证码' : '重新获取登录网址' }}</button>
          <button type="button" class="secondary-button" @click="emit('close')">关闭</button>
        </template>
      </div>
    </template>
  </JobProgress>
</template>
