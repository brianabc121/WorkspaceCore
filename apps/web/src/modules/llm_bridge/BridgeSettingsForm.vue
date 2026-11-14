<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { bridgeError, type Bridge, type BridgeStatus, type LlmBridgeApi } from './api'

// 一个 MCP 管理器的设置（保存在它自己那里）：并发、单个回合的时限、每个实例每小时的发送次数、单条指令的长度上限。
// 上下限与后端 BridgeSettingsPatch 一致。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge; status?: BridgeStatus; loading: boolean; loadError: unknown; canWrite: boolean }>()
const emit = defineEmits<{ reload: [] }>()

const FIELDS = [
  { key: 'concurrency', label: '同时进行的回合数', help: '每个账号同时进行的回合数（1–16）。', min: 1, max: 16 },
  { key: 'turn_timeout_seconds', label: '单个回合的时限（秒）', help: '一个回合最长跑多久（30–21600 秒）。', min: 30, max: 21600 },
  { key: 'send_per_hour', label: '每小时发送次数', help: '每个实例每小时最多发多少次指令（1–10000）。', min: 1, max: 10000 },
  { key: 'max_text_chars', label: '单条指令的字数上限', help: '一条指令最多多少个字符（1–1000000）。', min: 1, max: 1000000 },
] as const
type Key = (typeof FIELDS)[number]['key']
const form = reactive<Record<Key, string>>({ concurrency: '', turn_timeout_seconds: '', send_per_hour: '', max_text_chars: '' })
const settings = computed(() => (props.status?.reachable ? props.status.settings ?? null : null))
watch(settings, value => { if (value) for (const f of FIELDS) form[f.key] = String(value[f.key]) }, { immediate: true })

const error = ref(''), notice = ref(''), busy = ref(false)
async function save() {
  if (busy.value) return
  const body = {} as Record<Key, number>
  for (const f of FIELDS) {
    const n = Number(form[f.key])
    if (!String(form[f.key]).trim() || !Number.isInteger(n) || n < f.min || n > f.max) { error.value = `「${f.label}」要填 ${f.min}–${f.max} 之间的整数。`; notice.value = ''; return }
    body[f.key] = n
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
        <div v-for="f in FIELDS" :key="f.key" class="form-field">
          <label :for="`lb-set-${f.key}`">{{ f.label }}</label>
          <input :id="`lb-set-${f.key}`" v-model="form[f.key]" type="number" inputmode="numeric" step="1" :min="f.min" :max="f.max" required />
          <p class="form-help">{{ f.help }}</p>
        </div>
      </fieldset>
      <p class="form-help">Claude 订阅登录的风险确认：{{ settings.claude_subscription_ack ? '已确认' : '未确认（在“平台与账号”里用订阅方式登录 Claude 时确认）' }}。</p>
      <div v-if="canWrite" class="lb-actions"><button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在保存…' : '保存设置' }}</button></div>
    </form>
  </ManagementSection>
</template>
