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
  { key: 'concurrency', label: '每个平台同时进行的会话数', help: '一个平台（ChatGPT / Codex / Claude / CodeBuddy 各算各的）最多同时有几个会话在跑（1–16）；超过的排队等名额。调小可以降低账号被限制的风险。', min: 1, max: 16 },
  { key: 'min_start_interval_seconds', label: '两次开始的最小间隔（秒）', help: '同一个平台上两个会话开始处理之间至少隔几秒（0–60），几个名额不会同时起跑。', min: 0, max: 60 },
  { key: 'turn_timeout_seconds', label: '单个回合的时限（秒）', help: '一个回合最长跑多久（30–21600 秒）。', min: 30, max: 21600 },
  { key: 'send_per_hour', label: '每小时发送次数', help: '每个实例每小时最多发多少次指令（1–10000）。', min: 1, max: 10000 },
  { key: 'max_text_chars', label: '单条指令的字数上限', help: '一条指令最多多少个字符（1–1000000）。', min: 1, max: 1000000 },
  { key: 'retention_days', label: '对话内容保留天数', help: '按会话最后活动的时间算（0–3650）；0 是一直保留。到期只清对话内容（提问、回答、过程、附件），会话、每个回合的状态和用量留着。每 6 小时清一次，清掉的找不回来。', min: 0, max: 3650 },
  { key: 'auto_resume_max', label: '同一次中断最多自动继续几次', help: '自动继续后又被额度打断，算同一次中断；超过这个次数（1–10）就不再自动继续，要人点“现在就试”。', min: 1, max: 10 },
] as const
type Key = (typeof FIELDS)[number]['key']
const form = reactive<Record<Key, string>>({ concurrency: '', min_start_interval_seconds: '', turn_timeout_seconds: '', send_per_hour: '', max_text_chars: '', retention_days: '', auto_resume_max: '' })
// 额度用完后的自动继续：开关和发的那句话
const resume = reactive({ on: true, text: '' })
// 会话纪要（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2、5.3）：默认开；会话内容按保留期清掉时纪要跟着进回收站
const digest = reactive({ on: true, follow: true })
const settings = computed(() => (props.status?.reachable ? props.status.settings ?? null : null))
watch(settings, value => {
  if (!value) return
  for (const f of FIELDS) form[f.key] = String(value[f.key])
  Object.assign(resume, { on: value.auto_resume, text: value.resume_text })
  Object.assign(digest, { on: value.digest_enabled, follow: value.digest_follow_retention })
}, { immediate: true })
// 自动压缩的触发点（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 4.2）：默认 0（用平台自己的，不改）。
// 旧版本的管理器没有（为空）：不显示；只在改了的时候提交
const AUTOCOMPACT = [
  { key: 'autocompact_claude_percent', label: 'Claude：上下文用到百分之多少时自动压缩', help: '0 是用 Claude 自己的（默认）；1–100。调早了每回合便宜，但压缩更频繁、摘要一轮轮会走样。', min: 0, max: 100 },
  { key: 'autocompact_workbuddy_percent', label: 'CodeBuddy：上下文用到百分之多少时自动压缩', help: '0 是用 CodeBuddy 自己的（默认，通常在 70%–92%）；1–100。', min: 0, max: 100 },
  { key: 'autocompact_codex_tokens', label: 'Codex：上下文到多少 token 时自动压缩', help: '0 是用 Codex 自己的（默认）；按 token 数填，例如 150000。只对新建或重新载入的会话生效。', min: 0, max: 10000000 },
] as const
type AutoKey = (typeof AUTOCOMPACT)[number]['key']
const auto = reactive<Record<AutoKey, string>>({ autocompact_claude_percent: '', autocompact_workbuddy_percent: '', autocompact_codex_tokens: '' })
watch(settings, value => { if (value) for (const f of AUTOCOMPACT) auto[f.key] = value[f.key] == null ? '' : String(value[f.key]) }, { immediate: true })
const autoFields = computed(() => AUTOCOMPACT.filter(f => settings.value?.[f.key] != null))
// 压缩时的钩子（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 4.3）：默认关；旧版本的管理器没有（为空）就不显示
const hooks = ref(false)
watch(settings, value => { hooks.value = !!value?.hooks_enabled }, { immediate: true })
const hasHooks = computed(() => settings.value?.hooks_enabled != null)
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
  // 只在改了的时候提交：旧版本的管理器不认识这两项
  if (digest.on !== settings.value?.digest_enabled) body.digest_enabled = digest.on
  if (digest.follow !== settings.value?.digest_follow_retention) body.digest_follow_retention = digest.follow
  for (const f of autoFields.value) {
    const n = Number(auto[f.key])
    if (!String(auto[f.key]).trim() || !Number.isInteger(n) || n < f.min || n > f.max) { error.value = `「${f.label}」要填 ${f.min}–${f.max} 之间的整数。`; notice.value = ''; return }
    if (n !== settings.value?.[f.key]) body[f.key] = n
  }
  if (hasHooks.value && hooks.value !== settings.value?.hooks_enabled) body.hooks_enabled = hooks.value
  busy.value = true; error.value = notice.value = ''
  try { await props.api.saveSettings(props.bridge.id, body); notice.value = '设置已保存。' } catch (e) { error.value = bridgeError(e) } finally { busy.value = false }
}
</script>

<template>
  <ManagementSection title="运行设置" :label="`运行设置：${bridge.name}`" description="这些设置保存在这个 MCP 管理器里，对它控制的所有平台生效。">
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
      <fieldset class="lb-checks" :disabled="!canWrite || busy" aria-label="会话纪要">
        <legend>会话纪要</legend>
        <label class="check-field"><input v-model="digest.on" type="checkbox" />把会话的重点自动整理成纪要，存进知识库的“会话纪要”文件夹</label>
        <p class="form-help">会话有 3 个以上回合，并且平台压缩了上下文、会话归档、或者 24 小时没有新回合时整理一次。按记录排好（每一问、回答的开头、改过的文件、跑过的命令），不调模型、不花 token。纪要标为“未确认”：检索时排在最后，默认不会被带进提问；值得留下的可以转成正式笔记。智能体的会话放进它自己的知识库，人的放进公共知识库，关联了项目的放进项目知识库。</p>
        <label class="check-field"><input v-model="digest.follow" type="checkbox" />会话内容按保留期清掉时，纪要一起放进回收站</label>
      </fieldset>
      <fieldset v-if="autoFields.length" class="lb-checks lb-grid" :disabled="!canWrite || busy" aria-label="自动压缩的位置">
        <legend>自动压缩的位置</legend>
        <p class="form-help">三个平台都会在上下文快满时自动压缩。默认不改；需要每回合更省的可以调早。</p>
        <div v-for="f in autoFields" :key="f.key" class="form-field">
          <label :for="`lb-set-${f.key}`">{{ f.label }}</label>
          <input :id="`lb-set-${f.key}`" v-model="auto[f.key]" type="number" inputmode="numeric" step="1" :min="f.min" :max="f.max" />
          <p class="form-help">{{ f.help }}</p>
        </div>
      </fieldset>
      <fieldset v-if="hasHooks" class="lb-checks" :disabled="!canWrite || busy" aria-label="压缩时的钩子">
        <legend>压缩时的钩子</legend>
        <label class="check-field"><input v-model="hooks" type="checkbox" />Claude 和 CodeBuddy 压缩上下文时，提醒它保留要紧的内容，并把它写的摘要记下来</label>
        <p class="form-help">默认关。打开后：压缩前告诉平台要保留目标、做过的决定、没解决的问题和改过的文件，摘要按这四栏写；压缩后把平台写的摘要存在管理器里，用在会话纪要和“带着摘要另开会话”上（不用再让模型写一遍）。只对之后开始的回合生效；用真实账号核对前建议先在一个管理器上试。Codex 不支持。</p>
      </fieldset>
      <p class="form-help">Claude 订阅登录的风险确认：{{ settings.claude_subscription_ack ? '已确认' : '未确认（在“平台与账号”里用订阅方式登录 Claude 时确认）' }}。</p>
      <div v-if="canWrite" class="lb-actions"><button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在保存…' : '保存设置' }}</button></div>
    </form>
  </ManagementSection>
</template>
