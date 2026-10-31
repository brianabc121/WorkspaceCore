<script setup lang="ts">
import { ref } from 'vue'
import type { Schema } from '../../services/api'
import ModalPanel from '../../components/ModalPanel.vue'
import { safeUrl } from '../../widgets/registry'
const props = defineProps<{ connection: Schema['AutomationConnectionOut']; busy: boolean; locked: boolean; error: string }>()
const emit = defineEmits<{ close: []; save: [body: Schema['AutomationConnectionUpsert']]; reload: [] }>()
const base = ref(props.connection.base_url ?? ''), publicUrl = ref(props.connection.public_url ?? ''), key = ref(''), secret = ref(''), enabled = ref(props.connection.status !== 'disabled'), validation = ref('')
function submit() {
  if (props.busy || props.locked) return
  validation.value = ''
  if (!safeUrl(base.value) || publicUrl.value && !safeUrl(publicUrl.value)) { validation.value = '请填写无凭据的 http(s) 服务地址。'; return }
  if ((!props.connection.configured || key.value) && key.value.length < 20 || (!props.connection.configured || secret.value) && secret.value.length < 16) { validation.value = '新连接需要 API Key（至少20字符）与共享密钥（至少16字符）；修改时可留空保留。'; return }
  const body: Schema['AutomationConnectionUpsert'] = props.connection.configured ? { version: props.connection.version! } : {}
  if (!props.connection.configured || base.value !== props.connection.base_url) body.base_url = base.value
  if (publicUrl.value && publicUrl.value !== props.connection.public_url) body.public_url = publicUrl.value
  if (key.value) body.api_key = key.value
  if (secret.value) body.trigger_secret = secret.value
  if (!props.connection.configured || enabled.value !== (props.connection.status !== 'disabled')) body.enabled = enabled.value
  if (Object.keys(body).filter(k => k !== 'version').length === 0) { validation.value = '请至少修改一项。'; return }
  key.value = ''; secret.value = ''; emit('save', body)
}
</script>
<template>
  <ModalPanel title="配置 n8n 连接" :busy="busy" @close="!busy && $emit('close')">
    <p class="form-help">API Key 仅授予 workflow:list、workflow:read、execution:list、execution:read。密钥只写不读，提交后立即清空；修改时留空保留。</p>
    <form novalidate @submit.prevent="submit"><label class="form-field">服务地址<input v-model="base" :disabled="busy || locked" autocomplete="off" maxlength="2048"></label><label class="form-field">编辑器公开地址<input v-model="publicUrl" :disabled="busy || locked" maxlength="2048"></label><label class="form-field">API Key<input v-model="key" :disabled="busy || locked" type="password" autocomplete="new-password" maxlength="4096"></label><label class="form-field">触发共享密钥<input v-model="secret" :disabled="busy || locked" type="password" autocomplete="new-password" maxlength="512"></label><label><input v-model="enabled" :disabled="busy || locked" type="checkbox">启用连接</label><p class="form-help">API Key：{{ connection.api_key_set ? '已设置' : '未设置' }}；共享密钥：{{ connection.trigger_secret_set ? '已设置' : '未设置' }}</p><p v-if="validation || error" role="alert">{{ validation || error }}</p><div class="form-actions"><button class="primary-button" :disabled="busy || locked" type="submit">保存连接</button><button v-if="locked" class="secondary-button" type="button" :disabled="busy" @click="$emit('reload')">重新加载最新版本</button></div></form>
  </ModalPanel>
</template>
