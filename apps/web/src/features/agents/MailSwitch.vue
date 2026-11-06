<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'

// 实例卡片里的“邮件权限”（T-0130，MAIL_PLAN 第 5 节）：查询邮件默认打开；发送邮件默认关闭，
// 打开时必须选收件人范围（任何地址，或只限列出的地址 / 域名）。发送直接生效、不审批（例外七）。
const props = defineProps<{ instance: Schema['AgentInstanceOut']; busy: boolean }>()
const emit = defineEmits<{ save: [body: Omit<Schema['AgentMailPut'], 'version'>] }>()
const mail = computed(() => props.instance.mail)
const read = ref(true), send = ref(false), scope = ref<'any' | 'listed'>('listed'), recipients = ref(''), error = ref('')
function reset() {
  read.value = mail.value.read_enabled; send.value = mail.value.send_enabled
  scope.value = mail.value.send_scope ?? 'listed'; recipients.value = mail.value.recipients.join('\n'); error.value = ''
}
watch(mail, reset, { immediate: true })
const list = computed(() => recipients.value.split(/[\n,，;；\s]+/).map(s => s.trim()).filter(Boolean))
const valid = (entry: string) => /^(?:[^@\s]+)?@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/.test(entry)
const readOnly = computed(() => props.instance.scope !== 'read_write')
const summary = computed(() => {
  const parts = [mail.value.read_enabled ? '能查询邮件' : '不能查询邮件']
  if (mail.value.send_enabled) parts.push(mail.value.send_scope === 'any' ? '能发给任何地址' : `只能发给 ${mail.value.recipients.join('、')}`)
  else parts.push('不能发信')
  return parts.join('；')
})
function save() {
  error.value = ''
  const entries = list.value
  if (send.value && scope.value === 'listed' && !entries.length) { error.value = '选了“只限下列地址”就至少要填一个地址或域名。'; return }
  const bad = entries.find(e => !valid(e))
  if (scope.value === 'listed' && bad) { error.value = `不是有效的地址或域名：${bad}`; return }
  emit('save', { read_enabled: read.value, send_enabled: send.value, send_scope: scope.value, recipients: scope.value === 'listed' ? entries : [] })
}
</script>

<template>
  <section class="agent-section" aria-label="邮件权限">
    <h3>邮件权限：{{ summary }}</h3>
    <form class="mail-form" @submit.prevent="save">
      <label class="check-field"><input v-model="read" type="checkbox" /> 允许查询邮件（mail_accounts / mail_search / mail_get / mail_thread）</label>
      <label class="check-field"><input v-model="send" type="checkbox" :disabled="readOnly && !mail.send_enabled" /> 允许发送邮件（mail_send，直接发出、不审批）</label>
      <template v-if="send">
        <fieldset class="mail-scope">
          <legend>收件人范围</legend>
          <label class="check-field"><input v-model="scope" type="radio" value="listed" /> 只限下列地址或域名</label>
          <label class="check-field"><input v-model="scope" type="radio" value="any" /> 任何地址</label>
        </fieldset>
        <label v-if="scope === 'listed'" class="form-field"><span>地址（a@b.com）或域名（@b.com），每行一个，最多 50 条</span><textarea v-model="recipients" rows="3" /></label>
      </template>
      <p v-if="error" role="alert" class="feedback">{{ error }}</p>
      <div class="form-actions"><button type="submit" class="secondary-button" :disabled="busy">保存邮件权限</button></div>
    </form>
    <p class="form-help">只能用“设置 → 邮件”里允许智能体访问的邮箱，拿不到 Google 令牌，也不能删除或修改邮件。发信过密钥检查、每小时最多 10 封、每天 50 封，每封都写执行记录并通知管理员。<template v-if="readOnly"> 这个实例的工具权限是只读，打不开发送。</template><template v-if="mail.send_enabled && mail.send_at"> 发送权限打开于 {{ formatTime(mail.send_at) }}。</template></p>
  </section>
</template>
