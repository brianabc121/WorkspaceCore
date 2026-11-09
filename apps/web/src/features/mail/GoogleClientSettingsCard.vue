<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { Check, Copy, Info, KeyRound, Pencil } from '@lucide/vue'
import type { Schema } from '../../services/api'
import ManagementSection from '../../components/management/ManagementSection.vue'
import DataState from '../../components/DataState.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { isVersionConflict, mailError, useMailActions } from './api'

// 邮件设置 → Google OAuth 客户端（T-0194，效果图 05）：是否已配置、客户端 ID、密钥只显示“是否已设置”、
// 授权回调地址（来自接口，可复制）。修改时客户端密钥留空表示保持原值；输入的密钥只在这个组件的内存里，
// 提交、取消后清空，不回填、不进缓存或地址栏。
const props = defineProps<{ scope: string; settings: Schema['MailSettingsOut'] | undefined; loading: boolean; loadError: unknown }>()
const emit = defineEmits<{ retry: [] }>()
const actions = useMailActions(props.scope)
const editing = ref(false), clientId = ref(''), clientSecret = ref('')
const busy = ref(false), error = ref(''), notice = ref(''), copied = ref(false)
const idInput = ref<HTMLInputElement | null>(null), editButton = ref<HTMLButtonElement | null>(null)
// 还没配置时直接显示表单；配置过的点“修改客户端”才打开
const showForm = computed(() => !!props.settings && (editing.value || !props.settings.configured))

function open() {
  clientId.value = props.settings?.client_id ?? ''
  clientSecret.value = ''; error.value = ''; notice.value = ''; editing.value = true
  void nextTick(() => idInput.value?.focus())
}
function close() {
  editing.value = false; clientSecret.value = ''; error.value = ''
  void nextTick(() => editButton.value?.focus())
}
async function save() {
  if (busy.value || !props.settings) return
  const id = clientId.value.trim(), secret = clientSecret.value.trim()
  error.value = ''; notice.value = ''
  if (id.length < 10) { error.value = '请填写 Google OAuth 客户端 ID。'; return }
  if (!props.settings.client_secret_set && !secret) { error.value = '第一次设置要填客户端密钥。'; return }
  busy.value = true
  try {
    await actions.saveSettings({ client_id: id, ...(secret ? { client_secret: secret } : {}), version: props.settings.version ?? null })
    editing.value = false; notice.value = 'OAuth 客户端已保存'
  } catch (cause) {
    if (isVersionConflict(cause)) { error.value = 'OAuth 客户端刚被其他人修改：已载入最新设置，请核对后再保存。'; emit('retry') }
    else error.value = mailError(cause)
  } finally {
    // 提交过就清空密钥输入，不论成功与否
    clientSecret.value = ''; busy.value = false
  }
}
async function copyRedirect() {
  const uri = props.settings?.redirect_uri
  if (!uri) return
  try { await navigator.clipboard.writeText(uri); copied.value = true; setTimeout(() => { copied.value = false }, 2000) }
  catch { error.value = '复制失败，请手动选中复制。' }
}
</script>

<template>
  <ManagementSection title="Google OAuth 客户端" class="ms-client">
    <template #title>
      <span class="ms-client-icon" aria-hidden="true"><KeyRound /></span>Google OAuth 客户端
      <span v-if="settings" class="asv-state" :class="settings.configured ? 'tone-green' : 'tone-amber'">{{ settings.configured ? '已配置' : '未配置' }}</span>
    </template>
    <template v-if="settings?.configured && !editing" #actions>
      <button ref="editButton" type="button" class="secondary-button" @click="open"><Pencil aria-hidden="true" />修改客户端</button>
    </template>
    <DataState :loading="loading" :error="loadError" :empty="false" empty-text="" :stale="!!settings" @retry="emit('retry')" />
    <template v-if="settings">
      <dl class="ms-facts">
        <div><dt>客户端 ID</dt><dd>{{ settings.client_id ?? '未设置' }}</dd></div>
        <div><dt>客户端密钥</dt><dd>{{ settings.client_secret_set ? '已设置 · 不回显' : '未设置' }}</dd></div>
        <div>
          <dt>授权回调地址</dt>
          <dd class="ms-copy">
            <code>{{ settings.redirect_uri }}</code>
            <button type="button" class="icon-button" :aria-label="copied ? '已复制授权回调地址' : '复制授权回调地址'" :title="copied ? '已复制' : '复制'" @click="copyRedirect">
              <Check v-if="copied" aria-hidden="true" /><Copy v-else aria-hidden="true" />
            </button>
          </dd>
        </div>
      </dl>
      <p class="mg-hint"><Info aria-hidden="true" />授权范围：读取邮件 / 发送邮件。当前不申请删除或修改邮件的权限。</p>
      <form v-if="showForm" class="ms-client-form" aria-label="Google OAuth 客户端设置" @submit.prevent="save">
        <p class="form-help">在 Google Cloud 控制台建一个“Web 应用”类型的 OAuth 客户端并打开 Gmail API，把上面的授权回调地址登记进去，再把客户端 ID 和密钥填在这里。</p>
        <div class="ms-form-row mg-stack-narrow">
          <label class="form-field"><span>客户端 ID</span><input ref="idInput" v-model="clientId" autocomplete="off" maxlength="200" placeholder="1234567890-xxxx.apps.googleusercontent.com" /></label>
          <label class="form-field"><span>客户端密钥</span><input v-model="clientSecret" type="password" autocomplete="new-password" maxlength="200" :placeholder="settings.client_secret_set ? '已设置；不填表示保持不变' : '第一次必填'" /></label>
        </div>
        <div class="form-actions">
          <button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在保存…' : '保存 OAuth 客户端' }}</button>
          <button v-if="settings.configured" type="button" class="secondary-button" :disabled="busy" @click="close">取消</button>
        </div>
      </form>
    </template>
    <ActionFeedback :error="error" :notice="notice" />
  </ManagementSection>
</template>
