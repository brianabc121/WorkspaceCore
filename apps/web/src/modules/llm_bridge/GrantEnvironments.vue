<script setup lang="ts">
import { computed, toRef } from 'vue'
import { SANDBOX, bridgeError, platformName, type Bridge, type Grant, type LlmBridgeApi } from './api'

// 一个实例在一个 MCP 管理器上能用哪些开发环境（T-0247）。一个都不勾 = 不能新建开发会话。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge; grant: Grant }>()
defineEmits<{ toggle: [environment: string, on: boolean] }>()
const environments = props.api.useEnvironments(toRef(() => props.bridge.id))
const list = computed(() => (environments.data.value ?? []).filter(e => e.enabled))
const sandbox = (id: string) => (SANDBOX as Record<string, string>)[id] ?? id
const checked = (event: Event) => (event.target as HTMLInputElement).checked
</script>

<template>
  <div class="lb-grant-envs" role="group" :aria-label="`${grant.instance_name}在「${bridge.name}」上能用的开发环境`">
    <span class="lb-grant-envs-name">{{ bridge.name }}</span>
    <span v-if="environments.isPending.value" role="status" class="form-help">正在读取…</span>
    <span v-else-if="environments.error.value" role="alert" class="form-help">读不到开发环境：{{ bridgeError(environments.error.value) }}</span>
    <span v-else-if="!list.length" class="form-help">还没有开发环境</span>
    <label v-for="e in list" v-else :key="e.id" class="check-field">
      <input type="checkbox" :checked="grant.environment_ids.includes(e.id)" @change="$emit('toggle', e.id, checked($event))" />
      {{ e.name }}（{{ platformName(e.platform) }}，{{ sandbox(e.sandbox) }}）
    </label>
  </div>
</template>
