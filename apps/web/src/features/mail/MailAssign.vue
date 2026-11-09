<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Schema } from '../../services/api'
import type { MailAccount } from './api'

// 设置 → 邮件：这个邮箱分配给哪些智能体（T-0132；T-0194 改版）。没分配的智能体看不到这个邮箱；
// 分配后能不能查、能不能发，仍按每个实例自己的邮件开关（“OpenClaw 智能体”页），分配不会打开发信。
// 勾选是草稿：只属于这一个邮箱（换邮箱时组件整个重建），列表刷新不抹掉没保存的勾选。
// 有草稿时服务器上的分配被别人改了：保存前先提示核对，不直接覆盖。
const props = defineProps<{ account: MailAccount; agents: Schema['MailAgentRef'][]; loading: boolean; loadError: string; busy: boolean }>()
const emit = defineEmits<{ save: [instanceIds: string[]] }>()
const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every(id => b.includes(id))
const latest = computed(() => props.account.agents.map(a => a.id))
const picked = ref<string[]>([...latest.value])
// 草稿基于的分配：服务器上的分配与它不同，说明在编辑期间被改过
let base = [...latest.value]
const changed = ref('')
watch(() => [...latest.value].sort().join(','), () => {
  if (sameSet(picked.value, latest.value)) base = [...latest.value]
  else if (sameSet(picked.value, base)) { picked.value = [...latest.value]; base = [...latest.value] }
})
const dirty = computed(() => !sameSet(picked.value, latest.value))
const summary = computed(() => props.account.agents.length
  ? `能用这个邮箱的智能体：${props.account.agents.map(a => a.name).join('、')}`
  : '还没有分配给智能体：智能体看不到这个邮箱')
function save() {
  if (!sameSet(base, latest.value)) {
    changed.value = `这个邮箱的分配刚被其他人修改，现在是：${props.account.agents.map(a => a.name).join('、') || '未分配'}。请核对勾选后再点“保存分配”。`
    base = [...latest.value]
    return
  }
  changed.value = ''
  emit('save', [...picked.value])
}
</script>

<template>
  <fieldset class="ms-block ms-assign" :aria-label="`分配 ${account.email} 给智能体`">
    <legend><h3>分配给智能体</h3></legend>
    <p class="form-help">选择可以使用这个邮箱的智能体。分配后仍受各实例的查询与发信开关约束，不会自动打开发信。</p>
    <p class="ms-summary">{{ summary }}。</p>
    <p v-if="loading" role="status" class="form-help">正在加载智能体…</p>
    <p v-else-if="loadError" role="alert" class="form-help">{{ loadError }}</p>
    <p v-else-if="!agents.length" class="form-help">还没有登记 OpenClaw 智能体。</p>
    <div v-else class="ms-assign-list">
      <label v-for="agent in agents" :key="agent.id" class="check-field"><input v-model="picked" type="checkbox" :value="agent.id" :disabled="busy" /> {{ agent.name }}</label>
    </div>
    <p v-if="changed" role="alert" class="feedback">{{ changed }}</p>
    <button v-if="agents.length" type="button" class="primary-button ms-wide" :disabled="busy || !dirty" @click="save">{{ busy ? '正在保存…' : '保存分配' }}</button>
  </fieldset>
</template>
