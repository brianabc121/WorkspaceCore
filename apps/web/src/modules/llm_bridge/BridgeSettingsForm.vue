<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ChevronRight, FileText, Info, RefreshCw, Settings, SlidersHorizontal } from '@lucide/vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { bridgeError, type Bridge, type BridgeStatus, type LlmBridgeApi, type SettingsPatch } from './api'

// 一个 MCP 管理器的设置（保存在它自己那里）：每个平台同时进行的会话数、两次开始的最小间隔（限流，T-0236）、
// 单个回合的时限、每个实例每小时的发送次数、单条指令的长度上限；额度用完后的自动继续（T-0237）。
// 上下限与后端 BridgeSettingsPatch 一致。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge; status?: BridgeStatus; loading: boolean; loadError: unknown; canWrite: boolean }>()
const emit = defineEmits<{ reload: [] }>()

// 按效果图（T-0289）分成四张卡片：运行控制、额度恢复后自动继续、会话与保留、高级设置；底部固定“取消更改 / 保存设置”。
// 标签用效果图的短说法，完整的说明在悬停提示里；字段、上下限和提交的内容和原来一样。
const FIELDS = [
  { key: 'concurrency', label: '每个平台并行会话', unit: '个', short: '超出上限的会话自动排队。', help: '一个平台（Codex / Claude / CodeBuddy 各算各的）最多同时有几个会话在跑（1–16）；超过的排队等名额。调小可以降低账号被限制的风险。', min: 1, max: 16 },
  { key: 'min_start_interval_seconds', label: '会话启动间隔', unit: '秒', short: '同一平台两次开始的最小间隔。', help: '同一个平台上两个会话开始处理之间至少隔几秒（0–60），几个名额不会同时起跑。', min: 0, max: 60 },
  { key: 'turn_timeout_seconds', label: '单回合时限', unit: '秒', short: '超时后结束当前回合。', help: '一个回合最长跑多久（30–21600 秒）。', min: 30, max: 21600 },
  { key: 'send_per_hour', label: '每实例每小时发送', unit: '次', short: '限制每个实例的请求频率。', help: '每个实例每小时最多发多少次指令（1–10000）。', min: 1, max: 10000 },
  { key: 'max_text_chars', label: '单条指令字数上限', unit: '字', short: '一条指令最多多少个字符。', help: '一条指令最多多少个字符（1–1000000）。', min: 1, max: 1000000 },
  { key: 'retention_days', label: '对话内容保留', unit: '天', short: '到期只清对话内容。', help: '按会话最后活动的时间算（0–3650）；0 是一直保留。到期只清对话内容（提问、回答、过程、附件），会话、每个回合的状态和用量留着。每 6 小时清一次，清掉的找不回来。', min: 0, max: 3650 },
  { key: 'auto_resume_max', label: '同次中断最多继续', unit: '次', short: '达到次数上限后，需手动继续。', help: '自动继续后又被额度打断，算同一次中断；超过这个次数（1–10）就不再自动继续，要人点“现在就试”。', min: 1, max: 10 },
] as const
type Key = (typeof FIELDS)[number]['key']
const field = (key: Key) => FIELDS.find(f => f.key === key)!
const CONTROL: Key[] = ['concurrency', 'min_start_interval_seconds', 'turn_timeout_seconds', 'send_per_hour']
const form = reactive<Record<Key, string>>({ concurrency: '', min_start_interval_seconds: '', turn_timeout_seconds: '', send_per_hour: '', max_text_chars: '', retention_days: '', auto_resume_max: '' })
// 额度用完后的自动继续：开关和发的那句话
const resume = reactive({ on: true, text: '' })
// 会话纪要（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2、5.3）：默认开；会话内容按保留期清掉时纪要跟着进回收站
const digest = reactive({ on: true, follow: true })
const settings = computed(() => (props.status?.reachable ? props.status.settings ?? null : null))
// 对话内容保留：常用的几档用下拉选，别的天数选“自定义”再填
const RETENTION = [{ days: '0', name: '一直保留' }, { days: '7', name: '7 天' }, { days: '30', name: '30 天' }, { days: '90', name: '90 天' }, { days: '180', name: '180 天' }, { days: '365', name: '一年' }]
const customRetention = ref(false)
const retentionChoice = computed({
  get: () => (customRetention.value ? 'custom' : form.retention_days),
  set: (value: string) => { customRetention.value = value === 'custom'; if (value !== 'custom') form.retention_days = value },
})
// 自动压缩的触发点（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 4.2）：默认 0（用平台自己的，不改）。
// 旧版本的管理器没有（为空）：不显示；只在改了的时候提交
const AUTOCOMPACT = [
  { key: 'autocompact_claude_percent', label: 'Claude：上下文用到百分之多少时自动压缩', help: '0 是用 Claude 自己的（默认）；1–100。调早了每回合便宜，但压缩更频繁、摘要一轮轮会走样。', min: 0, max: 100 },
  { key: 'autocompact_workbuddy_percent', label: 'CodeBuddy：上下文用到百分之多少时自动压缩', help: '0 是用 CodeBuddy 自己的（默认，通常在 70%–92%）；1–100。', min: 0, max: 100 },
  { key: 'autocompact_codex_tokens', label: 'Codex：上下文到多少 token 时自动压缩', help: '0 是用 Codex 自己的（默认）；按 token 数填，例如 150000。只对新建或重新载入的会话生效。', min: 0, max: 10000000 },
] as const
type AutoKey = (typeof AUTOCOMPACT)[number]['key']
const auto = reactive<Record<AutoKey, string>>({ autocompact_claude_percent: '', autocompact_workbuddy_percent: '', autocompact_codex_tokens: '' })
const autoFields = computed(() => AUTOCOMPACT.filter(f => settings.value?.[f.key] != null))
const autoCustom = computed(() => autoFields.value.some(f => Number(auto[f.key]) > 0))
// 压缩时的钩子（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 4.3）：默认关；旧版本的管理器没有（为空）就不显示
const hooks = ref(false)
const hasHooks = computed(() => settings.value?.hooks_enabled != null)
// 旧版本的管理器没有“自动继续”：那三项不显示也不提交
const hasResume = computed(() => !!settings.value?.resume_text)
const fields = computed(() => FIELDS.filter(f => f.key !== 'auto_resume_max' || hasResume.value))
// 高级设置里按需展开的两项
const open = reactive({ autocompact: false, maxText: false })

// 读到设置时（以及“取消更改”时）把表单填回管理器上保存的值
function fill(value: NonNullable<typeof settings.value>) {
  for (const f of FIELDS) form[f.key] = String(value[f.key])
  customRetention.value = !RETENTION.some(r => r.days === String(value.retention_days))
  Object.assign(resume, { on: value.auto_resume, text: value.resume_text })
  Object.assign(digest, { on: value.digest_enabled, follow: value.digest_follow_retention })
  for (const f of AUTOCOMPACT) auto[f.key] = value[f.key] == null ? '' : String(value[f.key])
  hooks.value = !!value.hooks_enabled
}
watch(settings, value => { if (value) fill(value) }, { immediate: true })
function discard() { if (settings.value) fill(settings.value); error.value = ''; notice.value = '已取消更改。' }

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
    if (!text || text.length > 200) { error.value = '「继续时发送的消息」要填 1–200 个字。'; notice.value = ''; return }
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
  <section class="lb-settings" :aria-label="`运行设置：${bridge.name}`">
    <ActionFeedback :error="error" :notice="notice" />
    <p v-if="loading" role="status" class="data-state">正在读取设置…</p>
    <div v-else-if="!settings" class="data-state">
      <p role="alert">{{ props.loadError ? bridgeError(props.loadError) : `连不上这个 MCP 管理器：${status?.error || '没有给出原因'}` }}</p>
      <button type="button" class="secondary-button" @click="emit('reload')">重试</button>
    </div>
    <form v-else class="lb-settings-form" novalidate aria-label="MCP 管理器设置" @submit.prevent="save">
      <div class="lb-settings-grid">
        <!-- 运行控制 -->
        <fieldset class="lb-set-card" :disabled="!canWrite || busy" aria-label="运行控制">
          <div class="lb-set-head">
            <span class="lb-set-icon" aria-hidden="true"><SlidersHorizontal /></span>
            <div><h3>运行控制</h3><p>控制并行数量与发送频率</p></div>
          </div>
          <div class="lb-set-fields">
            <div v-for="key in CONTROL" :key="key" class="form-field lb-set-field">
              <label :for="`lb-set-${key}`">{{ field(key).label }}</label>
              <div class="lb-unit-input"><input :id="`lb-set-${key}`" v-model="form[key]" type="number" inputmode="numeric" step="1" :min="field(key).min" :max="field(key).max" required :title="field(key).help" /><span aria-hidden="true">{{ field(key).unit }}</span></div>
              <p class="form-help" :title="field(key).help">{{ field(key).short }}</p>
            </div>
          </div>
        </fieldset>

        <!-- 额度恢复后自动继续 -->
        <fieldset v-if="hasResume" class="lb-set-card" :disabled="!canWrite || busy" aria-label="额度用完后自动继续">
          <div class="lb-set-head">
            <span class="lb-set-icon" aria-hidden="true"><RefreshCw /></span>
            <div><h3>额度恢复后自动继续</h3><p>额度重置后，继续被中断的会话。</p></div>
            <label class="lb-switch-label"><input v-model="resume.on" type="checkbox" role="switch" class="lb-switch-input" /><span class="lb-sr">会话因为平台的额度用完被打断时，等额度重置后自动发一句话让它接着做</span></label>
          </div>
          <div class="lb-set-row">
            <label for="lb-set-auto_resume_max" :title="field('auto_resume_max').help">{{ field('auto_resume_max').label }}</label>
            <div class="lb-unit-input"><input id="lb-set-auto_resume_max" v-model="form.auto_resume_max" type="number" inputmode="numeric" step="1" :min="1" :max="10" required /><span aria-hidden="true">次</span></div>
          </div>
          <div class="form-field lb-set-field">
            <label for="lb-set-resume_text">继续时发送的消息</label>
            <textarea id="lb-set-resume_text" v-model="resume.text" rows="2" maxlength="200" required />
            <p class="form-help" title="到重置时间后，管理器替人往每个被打断的会话里发这句话，排在这个会话队列的最前面。关掉开关后不自动发，可以在聊天页点“现在就试”。">达到次数上限后，需手动继续；关掉开关也可以在聊天页点“现在就试”。</p>
          </div>
        </fieldset>

        <!-- 会话与保留 -->
        <fieldset class="lb-set-card" :disabled="!canWrite || busy" aria-label="会话与保留">
          <div class="lb-set-head">
            <span class="lb-set-icon" aria-hidden="true"><FileText /></span>
            <div><h3>会话与保留</h3><p>对话内容保留多久，会话纪要怎么整理</p></div>
          </div>
          <div class="lb-set-row">
            <div class="lb-set-text"><label for="lb-set-retention">{{ field('retention_days').label }}</label><p :title="field('retention_days').help">到期只清对话内容，会话和用量留着；清掉的找不回来。</p></div>
            <select id="lb-set-retention" v-model="retentionChoice">
              <option v-for="r in RETENTION" :key="r.days" :value="r.days">{{ r.name }}</option>
              <option value="custom">自定义天数…</option>
            </select>
          </div>
          <div v-if="customRetention" class="lb-set-row">
            <label for="lb-set-retention_days">保留天数</label>
            <div class="lb-unit-input"><input id="lb-set-retention_days" v-model="form.retention_days" type="number" inputmode="numeric" step="1" :min="0" :max="3650" required /><span aria-hidden="true">天</span></div>
          </div>
          <div class="lb-set-group" role="group" aria-label="会话纪要">
            <label class="lb-set-row lb-set-switch-row">
              <span class="lb-set-text"><strong>自动整理会话纪要</strong><span title="会话有 3 个以上回合，并且平台压缩了上下文、会话归档、或者 24 小时没有新回合时整理一次。按记录排好（每一问、回答的开头、改过的文件、跑过的命令），不调模型、不花 token。纪要标为“未确认”，值得留下的可以转成正式笔记。">把会话的重点自动整理成纪要，存进知识库，不额外消耗 token。</span></span>
              <input v-model="digest.on" type="checkbox" role="switch" class="lb-switch-input" />
            </label>
            <label class="lb-set-row lb-set-switch-row">
              <span class="lb-set-text"><strong>纪要随对话清理</strong><span>会话内容按保留期清掉时，纪要一起放进回收站。</span></span>
              <input v-model="digest.follow" type="checkbox" role="switch" class="lb-switch-input" />
            </label>
          </div>
        </fieldset>

        <!-- 高级设置：按需展开 -->
        <fieldset class="lb-set-card" :disabled="!canWrite || busy" aria-label="高级设置">
          <div class="lb-set-head">
            <span class="lb-set-icon" aria-hidden="true"><Settings /></span>
            <div><h3>高级设置</h3><p>平台专属参数按需展开</p></div>
          </div>
          <template v-if="autoFields.length">
            <button type="button" class="lb-set-row lb-set-more" :aria-expanded="open.autocompact" @click="open.autocompact = !open.autocompact">
              <span class="lb-set-text"><strong>上下文自动压缩</strong><span>{{ autoFields.map(f => f.label.split('：')[0]).join(' · ') }}</span></span>
              <span class="lb-set-value">{{ autoCustom ? '已自定义' : '跟随平台默认' }}</span><ChevronRight class="lb-set-chevron" aria-hidden="true" />
            </button>
            <div v-if="open.autocompact" class="lb-set-sub" role="group" aria-label="自动压缩的位置">
              <p class="form-help">三个平台都会在上下文快满时自动压缩。默认不改；需要每回合更省的可以调早。</p>
              <div v-for="f in autoFields" :key="f.key" class="form-field lb-set-field">
                <label :for="`lb-set-${f.key}`">{{ f.label }}</label>
                <input :id="`lb-set-${f.key}`" v-model="auto[f.key]" type="number" inputmode="numeric" step="1" :min="f.min" :max="f.max" />
                <p class="form-help">{{ f.help }}</p>
              </div>
            </div>
          </template>
          <div v-if="hasHooks" class="lb-set-group" role="group" aria-label="压缩时的钩子">
            <label class="lb-set-row lb-set-switch-row">
              <span class="lb-set-text"><strong>压缩时保留摘要</strong><span title="默认关。打开后：压缩前告诉平台要保留目标、做过的决定、没解决的问题和改过的文件，摘要按这四栏写；压缩后把平台写的摘要存在管理器里，用在会话纪要和“带着摘要另开会话”上（不用再让模型写一遍）。只对之后开始的回合生效；用真实账号核对前建议先在一个管理器上试。Codex 不支持。">适用于 Claude、CodeBuddy：压缩上下文时，提醒它保留要紧的内容，并把它写的摘要记下来</span></span>
              <input v-model="hooks" type="checkbox" role="switch" class="lb-switch-input" />
            </label>
          </div>
          <button type="button" class="lb-set-row lb-set-more" :aria-expanded="open.maxText" @click="open.maxText = !open.maxText">
            <span class="lb-set-text"><strong>{{ field('max_text_chars').label }}</strong></span>
            <span class="lb-set-value">{{ Number(form.max_text_chars || 0).toLocaleString() }} 字</span><ChevronRight class="lb-set-chevron" aria-hidden="true" />
          </button>
          <div v-if="open.maxText" class="lb-set-sub">
            <div class="form-field lb-set-field">
              <label for="lb-set-max_text_chars">{{ field('max_text_chars').label }}（{{ field('max_text_chars').min }}–{{ field('max_text_chars').max }}）</label>
              <div class="lb-unit-input"><input id="lb-set-max_text_chars" v-model="form.max_text_chars" type="number" inputmode="numeric" step="1" :min="1" :max="1000000" required /><span aria-hidden="true">字</span></div>
            </div>
          </div>
          <p class="lb-set-note"><Info aria-hidden="true" />订阅登录确认在“平台与账号”中管理（现在：{{ settings.claude_subscription_ack ? '已确认' : '未确认' }}）。</p>
        </fieldset>
      </div>

      <div v-if="canWrite" class="lb-set-bar">
        <p><Info aria-hidden="true" />设置仅作用于当前 MCP 管理器</p>
        <button type="button" class="secondary-button" :disabled="busy" @click="discard">取消更改</button>
        <button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在保存…' : '保存设置' }}</button>
      </div>
    </form>
  </section>
</template>
