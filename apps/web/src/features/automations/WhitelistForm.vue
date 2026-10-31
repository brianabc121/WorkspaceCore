<script setup lang="ts">
import { ref } from 'vue'
import type { Schema } from '../../services/api'
import { paramSpecs } from './validation'
import ModalPanel from '../../components/ModalPanel.vue'
const props = defineProps<{ item?: Schema['AutomationOut']; busy: boolean; locked: boolean; error: string }>()
const emit = defineEmits<{ close: []; save: [body: Schema['AutomationCreate'] | Schema['AutomationUpdate']] }>()
const name = ref(props.item?.name ?? ''), description = ref(props.item?.description ?? ''), workflow = ref(props.item?.workflow_id ?? ''), path = ref(props.item?.webhook_path ?? ''), enabled = ref(props.item?.enabled ?? true)
const params = ref((props.item?.params ?? []).map(p => ({ ...p, choicesText: p.choices?.join('\n') ?? '' }))), validation = ref('')
function add() { if (params.value.length < 20) params.value.push({ name: '', label: '', type: 'string', required: false, sensitive: false, max_length: null, choices: null, choicesText: '' }) }
function submit() {
  if (props.busy || props.locked) return
  validation.value = ''
  const specs = paramSpecs.safeParse(params.value.map(p => ({ name: p.name, label: p.label, type: p.type, required: p.required, sensitive: p.sensitive, max_length: p.type === 'string' ? p.max_length ?? null : null, choices: p.type === 'string' && p.choicesText.trim() ? p.choicesText.split('\n').map(c => c.trim()).filter(Boolean) : null })))
  if (!name.value.trim() || name.value.trim().length > 64 || !/^[A-Za-z0-9]{1,64}$/.test(workflow.value) || !/^[A-Za-z0-9_-]{1,128}$/.test(path.value) || !specs.success) { validation.value = '请检查名称、工作流 ID、触发路径与参数声明（标识符不可重复，最多20项）。'; return }
  const values = { name: name.value.trim(), description: description.value, webhook_path: path.value, enabled: enabled.value, params: specs.data }
  emit('save', props.item ? { ...values, version: props.item.version } : { ...values, workflow_id: workflow.value })
}
</script>
<template>
  <ModalPanel :title="item ? '编辑白名单工作流' : '登记工作流'" :busy="busy" @close="!busy && $emit('close')">
    <form novalidate @submit.prevent="submit">
      <label class="form-field">名称<input v-model="name" maxlength="64" :disabled="busy || locked"></label><label class="form-field">说明<textarea v-model="description" maxlength="500" :disabled="busy || locked"></textarea></label><label class="form-field">工作流 ID<input v-model="workflow" :readonly="!!item" :disabled="busy || locked"></label><label class="form-field">触发路径<input v-model="path" :disabled="busy || locked"></label><label><input v-model="enabled" type="checkbox" :disabled="busy || locked">启用工作流</label>
      <fieldset :disabled="busy || locked"><legend>触发参数</legend><section v-for="(p, index) in params" :key="index" class="parameter-editor"><label class="form-field">参数标识<input v-model="p.name"></label><label class="form-field">显示名称<input v-model="p.label"></label><label class="form-field">类型<select v-model="p.type" aria-label="类型"><option value="string">文本</option><option value="number">数字</option><option value="boolean">是 / 否</option></select></label><label><input v-model="p.required" type="checkbox">必填</label><label><input v-model="p.sensitive" type="checkbox">敏感值</label><template v-if="p.type === 'string'"><label class="form-field">最大长度<input v-model.number="p.max_length" type="number" min="1" max="2000"></label><label class="form-field">可选值（每行一个，留空不限）<textarea v-model="p.choicesText"></textarea></label></template><button type="button" class="secondary-button" @click="params.splice(index, 1)">移除参数</button></section><button type="button" class="secondary-button" :disabled="params.length >= 20" @click="add">添加参数</button></fieldset>
      <p v-if="validation || error" role="alert">{{ validation || error }}</p><button type="submit" class="primary-button" :disabled="busy || locked">保存工作流</button>
    </form>
  </ModalPanel>
</template>
