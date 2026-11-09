<script setup lang="ts">
import { ChevronRight, Send } from '@lucide/vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import NewsSecretInput from './NewsSecretInput.vue'
import { CHANNELS, type ChannelId, type NewsSettings, type PlainField } from './api'
import { emptySecret, type ChannelDraft, type FormPatch, type NewsForm, type SecretDraft } from './settingsForm'

// “模型与渠道”标签的推送渠道（T-0195）：九个渠道的开关、明文字段与只写的凭证（已设置只显示“已设置”，不回填原值）。
// “已启用 / 已设置”只说明配置了，不说明推送送达。只发出对草稿的修改，不保存。
const props = defineProps<{ form: NewsForm; saved: NewsSettings; disabled: boolean }>()
const emit = defineEmits<{ patch: [FormPatch] }>()
const target = (event: Event) => event.target as HTMLInputElement

function setChannel(channel: ChannelId, change: Partial<ChannelDraft>) {
  emit('patch', { channels: { ...props.form.channels, [channel]: { ...props.form.channels[channel], ...change } } })
}
function setPlain(channel: ChannelId, key: PlainField, value: string) { setChannel(channel, { [key]: value } as Partial<ChannelDraft>) }
function setSecret(channel: ChannelId, key: string, value: SecretDraft) {
  setChannel(channel, { secrets: { ...props.form.channels[channel].secrets, [key]: value } })
}
const secretsSet = (channel: ChannelId) => props.saved.channels[channel]?.secrets_set ?? []
</script>

<template>
  <ManagementSection title="推送渠道" label="推送渠道设置" description="凭证加密保存，页面上只显示“已设置”，不回显。早报总会推到工作台（早报页与站内通知），这里选的是额外推到哪里。">
    <template #title><span class="nws-title"><Send aria-hidden="true" />推送渠道</span></template>
    <fieldset class="nws-fieldset nws-channels" :disabled="disabled">
      <details v-for="def in CHANNELS" :key="def.id" class="nws-channel" :open="form.channels[def.id].enabled || undefined">
        <summary>
          <ChevronRight aria-hidden="true" />
          <span class="nws-channel-name">{{ def.name }}</span>
          <span class="asv-state" :class="form.channels[def.id].enabled ? 'tone-green' : 'tone-muted'">{{ form.channels[def.id].enabled ? '已启用' : '未启用' }}</span>
          <span v-if="secretsSet(def.id).length" class="asv-sub">凭证已设置</span>
        </summary>
        <div role="group" :aria-label="`推送渠道：${def.name}`" class="nws-channel-body">
          <p class="form-help">{{ def.help }}</p>
          <label class="check-field"><input type="checkbox" :checked="form.channels[def.id].enabled" @change="setChannel(def.id, { enabled: target($event).checked })" />启用{{ def.name }}</label>
          <div v-if="def.plain.length" class="nws-grid">
            <template v-for="field in def.plain" :key="field.key">
              <label v-if="field.key === 'msg_type'" class="form-field"><span>{{ field.label }}</span>
                <select :value="form.channels[def.id].msg_type" @change="setChannel(def.id, { msg_type: (target($event).value as ChannelDraft['msg_type']) })"><option value="markdown">Markdown</option><option value="text">文本</option></select>
              </label>
              <label v-else class="form-field"><span>{{ field.label }}</span><input :value="form.channels[def.id][field.key]" :inputmode="field.key === 'smtp_port' ? 'numeric' : undefined" :placeholder="field.placeholder" @input="setPlain(def.id, field.key, target($event).value)" /></label>
            </template>
          </div>
          <NewsSecretInput v-for="field in def.secrets" :key="field.key" :model-value="form.channels[def.id].secrets[field.key] ?? emptySecret()" :label="field.label" :is-set="secretsSet(def.id).includes(field.key)" :placeholder="field.placeholder" :disabled="disabled" @update:model-value="setSecret(def.id, field.key, $event)" />
        </div>
      </details>
    </fieldset>
  </ManagementSection>
</template>
