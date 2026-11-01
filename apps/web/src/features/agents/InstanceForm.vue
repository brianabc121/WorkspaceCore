<script setup lang="ts">
import { reactive } from 'vue'
import type { Schema } from '../../services/api'
import ModalPanel from '../../components/ModalPanel.vue'
import { NETWORK, ROLE, SCOPE } from './queries'

const props = defineProps<{ instance?: Schema['AgentInstanceOut']; busy: boolean; error: string }>()
const emit = defineEmits<{ close: []; save: [body: Schema['AgentInstanceCreate'] | Schema['AgentInstanceUpdate']] }>()
const form = reactive({
  name: props.instance?.name ?? '', description: props.instance?.description ?? '', im_nickname: props.instance?.im_nickname ?? '',
  network_mode: props.instance?.network_mode ?? 'intranet', scope: props.instance?.scope ?? 'read', max_role: props.instance?.max_role ?? 'member',
})
function submit() {
  const common = { name: form.name.trim(), description: form.description.trim(), scope: form.scope, max_role: form.max_role, im_nickname: form.im_nickname.trim() || null }
  if (props.instance) emit('save', { ...common, im_nickname: common.im_nickname ?? form.name.trim(), version: props.instance.version })
  else emit('save', { ...common, network_mode: form.network_mode })
}
</script>

<template>
  <ModalPanel :title="instance ? `编辑实例：${instance.name}` : '登记 OpenClaw 实例'" :busy="busy" @close="$emit('close')">
    <form class="im-group-form" @submit.prevent="submit">
      <label class="form-field"><span>名称 *</span><input v-model="form.name" required maxlength="64" autofocus /></label>
      <label class="form-field"><span>说明</span><textarea v-model="form.description" rows="2" maxlength="500" placeholder="所在服务器、用途等（纯文本）" /></label>
      <label class="form-field"><span>IM 昵称</span><input v-model="form.im_nickname" maxlength="32" placeholder="不填时用名称" /></label>
      <fieldset v-if="!instance" class="im-checks">
        <legend>网络模式（登记后不能修改）</legend>
        <label v-for="(label, value) in NETWORK" :key="value" class="check-field"><input v-model="form.network_mode" type="radio" :value="value" /> {{ label }}{{ value === 'public' ? '：经公网域名与双向证书连回工作台' : '：经私网地址连回工作台' }}</label>
      </fieldset>
      <label class="form-field"><span>工具权限</span><select v-model="form.scope"><option v-for="(label, value) in SCOPE" :key="value" :value="value">{{ label }}</option></select></label>
      <label class="form-field"><span>最高角色（实际取登记人与这里的较低者）</span><select v-model="form.max_role"><option v-for="(label, value) in ROLE" :key="value" :value="value">{{ label }}</option></select></label>
      <p v-if="error" role="alert">{{ error }}</p>
      <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy || !form.name.trim()">{{ instance ? '保存' : '登记' }}</button><button type="button" class="secondary-button" :disabled="busy" @click="$emit('close')">取消</button></div>
    </form>
  </ModalPanel>
</template>
