<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { bridgeError, type Bridge, type BridgeStatus, type LlmBridgeApi, type SettingsPatch } from './api'

// 一个 MCP 管理器的设置（保存在它自己那里）：每个平台同时进行的会话数、两次开始的最小间隔（限流，T-0236）、
// 单个回合的时限、每个实例每小时的发送次数、单条指令的长度上限；额度用完后的自动继续（T-0237）。
// 上下限与后端 BridgeSettingsPatch 一致。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge; status?: BridgeStatus; loading: boolean; loadError: unknown; canWrite: boolean }>()
const emit = defineEmits<{ reload: [] }>()

const FIELDS = [
  { key: 'concurrency', label: '每个平台同时进行的会话数', help: '一个平台（ChatGPT / Codex / Claude / WorkBuddy 各算各的）最多同时有几个会话在跑（1–16）；超过的排队等名额。调小可以降低账号被限制的风险。', min: 1, max: 16 },
  { key: 'min_start_interval_seconds', label: '两次开始的最小间隔（秒）', help: '同一个平台上两个会话开始处理之间至少隔几秒（0–60），几个名额不会同时起跑。', min: 0, max: 60 },
  { key: 'turn_timeout_seconds', label: '单个回合的时限（秒）', help: '一个回合最长跑多久（30–21600 秒）。', min: 30, max: 21600 },
  { key: 'send_per_hour', label: '每小时发送次数', help: '每个实例每小时最多发多少次指令（1–10000）。', min: 1, max: 10000 },
  { key: 'max_text_chars', label: '单条指令的字数上限', help: '一条指令最多多少个字符（1–1000000）。', min: 1, max: 1000000 },
  { key: 'auto_resume_max', label: '同一次中断最多自动继续几次', help: '自动继续后又被额度打断，算同一次中断；超过这个次数（1–10）就不再自动继续，要人点“现在就试”。', min: 1, max: 10 },
] as const
type Key = (typeof FIELDS)[number]['key']
const form = reactive<Record<Key, string>>({ concurrency: '', min_start_interval_seconds: '', turn_timeout_seconds: '', send_per_hour: '', max_text_chars: '', auto_resume_max: '' })
// 额度用完后的自动继续：开关和发的那句话
const resume = reactive({ on: true, text: '' })
const settings = computed(() => (props.status?.reachable ? props.status.settings ?? null : null))
watch(settings, value => {
  if (!value) return
  for (const f of FIELDS) form[f.key] = String(value[f.key])
  Object.assign(resume, { on: value.auto_resume, text: value.resume_text })
}, { immediate: true })
// 旧版本的管理器没有“自动继续”：那三项不显示也不提交
const hasResume = computed(() => !!settings.value?.resume_text)
const fields = computed(() => FIELDS.filter(f => f.key !== 'auto_resume_max' || hasResume.value))

const error = ref(''), notice = ref(''), busy = ref(false)
async function save() {
  if (busy.value) return
  const body: SettingsPatch = {}
  for (const f of fields.value) {
    const n = Number(form[f.key])
    if (!String(form[f.key]).trim() || !Number.isInteger(n) || n < f.min || n > f.max) { error.value = `「${f.label}」要填 ${f.min}–${f.max} 之间的整数。`; notice.value = ''; return }
    body[f.key] = n
  }
  if (hasResume.value) {
    const text = resume.text.trim()
    if (!text || text.length > 200) { error.value = '「自动继续时发的那句话」要填 1–200 个字。'; notice.value = ''; return }
    Object.assign(body, { auto_resume: resume.on, resume_text: text })
  }
  busy.value = true; error.value = notice.value = ''
  try { await props.api.saveSettings(props.bridge.id, body); notice.value = '设置已保存。' } catch (e) { error.value = bridgeError(e) } finally { busy.value = false }
}
</script>

<template>
  <ManagementSection title="设置" :label="`设置：${bridge.name}`" description="这些设置保存在这个 MCP 管理器里，对它控制的所有平台生效。">
    <ActionFeedback :error="error" :notice="notice" />
    <p v-if="loading" role="status" class="data-state">正在读取设置…</p>
    <div v-else-if="!settings" class="data-state">
      <p role="alert">{{ props.loadError ? bridgeError(props.loadError) : `连不上这个 MCP 管理器：${status?.error || '没有给出原因'}` }}</p>
      <button type="button" class="secondary-button" @click="emit('reload')">重试</button>
    </div>
    <form v-else class="asv-form lb-form" novalidate aria-label="MCP 管理器设置" @submit.prevent="save">
      <fieldset class="lb-checks lb-grid" :disabled="!canWrite || busy">
        <div v-for="f in fields" :key="f.key" class="form-field">
          <label :for="`lb-set-${f.key}`">{{ f.label }}</label>
          <input :id="`lb-set-${f.key}`" v-model="form[f.key]" type="number" inputmode="numeric" step="1" :min="f.min" :max="f.max" required />
          <p class="form-help">{{ f.help }}</p>
        </div>
      </fieldset>
      <fieldset v-if="hasResume" class="lb-checks" :disabled="!canWrite || busy" aria-label="额度用完后自动继续">
        <legend>额度用完后自动继续</legend>
        <label class="check-field"><input v-model="resume.on" type="checkbox" />会话因为平台的额度用完（例如 5 小时额度）被打断时，等额度重置后自动发一句话让它接着做</label>
        <div class="form-field">
          <label for="lb-set-resume_text">自动继续时发的那句话</label>
          <input id="lb-set-resume_text" v-model="resume.text" type="text" maxlength="200" required />
          <p class="form-help">到重置时间后，管理器替人往每个被打断的会话里发这句话，排在这个会话队列的最前面。关掉开关后不自动发，可以在聊天页点“现在就试”。</p>
        </div>
      </fieldset>
      <p class="form-help">Claude 订阅登录的风险确认：{{ settings.claude_subscription_ack ? '已确认' : '未确认（在“平台与账号”里用订阅方式登录 Claude 时确认）' }}。</p>
      <div v-if="canWrite" class="lb-actions"><button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在保存…' : '保存设置' }}</button></div>
    </form>
  </ManagementSection>
</template>
