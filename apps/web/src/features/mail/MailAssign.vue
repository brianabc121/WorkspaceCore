<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Schema } from '../../services/api'
import type { MailAccount } from './api'

// 设置 → 邮件：这个邮箱分配给哪些智能体（T-0132）。没分配的智能体看不到这个邮箱；
// 分配后能不能查、能不能发，仍按每个实例自己的邮件开关（“OpenClaw 实例”页）。
const props = defineProps<{ account: MailAccount; agents: Schema['MailAgentRef'][]; busy: boolean }>()
const emit = defineEmits<{ save: [instanceIds: string[]] }>()
const picked = ref<string[]>([])
watch(() => props.account, (account) => { picked.value = account.agents.map(a => a.id) }, { immediate: true })
const current = computed(() => new Set(props.account.agents.map(a => a.id)))
const dirty = computed(() => picked.value.length !== current.value.size || picked.value.some(id => !current.value.has(id)))
const summary = computed(() => props.account.agents.length
  ? `能用这个邮箱的智能体：${props.account.agents.map(a => a.name).join('、')}`
  : '还没有分配给智能体：智能体看不到这个邮箱')
</script>

<template>
  <fieldset class="mail-assign" :aria-label="`分配 ${account.email} 给智能体`">
    <legend>分配给智能体</legend>
    <p class="form-help">{{ summary }}。</p>
    <p v-if="!agents.length" class="form-help">还没有登记 OpenClaw 实例。</p>
    <div class="mail-assign-list">
      <label v-for="agent in agents" :key="agent.id" class="check-field"><input v-model="picked" type="checkbox" :value="agent.id" /> {{ agent.name }}</label>
    </div>
    <div v-if="agents.length" class="form-actions"><button type="button" class="secondary-button" :disabled="busy || !dirty" @click="emit('save', [...picked])">保存分配</button></div>
  </fieldset>
</template>
