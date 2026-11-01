<script setup lang="ts">
import { computed, reactive } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf } from '../../services/api'
import ModalPanel from '../../components/ModalPanel.vue'
import { useContacts } from './queries'
import { useImAction } from './action'

const props = defineProps<{ scope: string }>()
const emit = defineEmits<{ close: []; created: [conversationId: string] }>()
const auth = useAuthStore(), contacts = useContacts(props.scope)
const { busy, error, act, header } = useImAction(props.scope)
const form = reactive({ name: '', announcement: '', relay_enabled: true, instance_ids: [] as string[] })
const instances = computed(() => (contacts.data.value?.items ?? []).filter(c => c.instance_id && c.status !== 'disabled'))
// The group ID is derived from this key on the server, so retrying after an unknown result never creates two groups.
let key: string | null = null
async function submit() {
  if (!form.name.trim() || !form.instance_ids.length) return
  key ??= crypto.randomUUID()
  let created = ''
  await act(async () => {
    try {
      created = dataOf(await auth.api.POST('/api/v1/im/groups', { params: { header: { ...header, 'Idempotency-Key': key! } }, body: { ...form, name: form.name.trim(), announcement: form.announcement.trim() } })).conversation_id
    } catch (cause) {
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && cause.status !== 429) key = null
      throw cause
    }
  }, '群已创建')
  if (created) emit('created', created)
}
</script>

<template>
  <ModalPanel title="建群" :busy="busy" @close="$emit('close')">
    <form class="im-group-form" @submit.prevent="submit">
      <label class="form-field"><span>群名称 *</span><input v-model="form.name" required maxlength="64" autofocus /></label>
      <label class="form-field"><span>群公告</span><textarea v-model="form.announcement" rows="2" maxlength="1000" /></label>
      <fieldset class="im-checks">
        <legend>拉入的实例 *（至少一个）</legend>
        <p v-if="!instances.length" class="empty-state">没有可拉入的实例。</p>
        <label v-for="c in instances" :key="c.im_user_id" class="check-field"><input v-model="form.instance_ids" type="checkbox" :value="c.instance_id" /> {{ c.nickname }}</label>
      </fieldset>
      <label class="check-field"><input v-model="form.relay_enabled" type="checkbox" /> 允许实例之间接力</label>
      <p class="form-help">系统账号 ws_notice 会自动入群，用来发“自动接力已暂停”等提示。</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy || !form.name.trim() || !form.instance_ids.length">建群</button><button type="button" class="secondary-button" :disabled="busy" @click="$emit('close')">取消</button></div>
    </form>
  </ModalPanel>
</template>
