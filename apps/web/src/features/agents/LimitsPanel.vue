<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import { useAgentAction } from './action'

// 实例卡片里的“资源上限”（T-0145，D2：默认不限制）：OpenClaw 容器的内存（MiB）与 CPU（核）上限，
// 写进接入包的 compose.yaml，重新部署这个实例后生效。留空表示不限制。
const props = defineProps<{ instance: Schema['AgentInstanceOut'] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const { busy, error, notice, act, header } = useAgentAction(scope)
const mem = ref(''), cpus = ref('')
const limits = computed(() => props.instance.limits ?? { mem_limit_mb: null, cpus: null })
watch(limits, l => { mem.value = l.mem_limit_mb === null ? '' : String(l.mem_limit_mb); cpus.value = l.cpus === null ? '' : String(l.cpus) }, { immediate: true })
const memValue = computed(() => mem.value.trim() === '' ? null : Number(mem.value))
const cpuValue = computed(() => cpus.value.trim() === '' ? null : Number(cpus.value))
const memValid = computed(() => memValue.value === null || (Number.isInteger(memValue.value) && memValue.value >= 256 && memValue.value <= 1048576))
const cpuValid = computed(() => cpuValue.value === null || (Number.isFinite(cpuValue.value) && cpuValue.value >= 0.1 && cpuValue.value <= 256))
const changed = computed(() => memValue.value !== limits.value.mem_limit_mb || cpuValue.value !== limits.value.cpus)
const summary = computed(() => {
  const l = limits.value
  if (l.mem_limit_mb === null && l.cpus === null) return '不限制'
  return [l.mem_limit_mb !== null ? `内存 ${l.mem_limit_mb} MiB` : '内存不限', l.cpus !== null ? `CPU ${l.cpus} 核` : 'CPU 不限'].join('，')
})

async function reconcile() {
  await act(async () => dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/resources/reconcile', {
    params: { header, path: { instance_id: props.instance.id } }, body: { version: props.instance.version },
  })), '实际资源已核对，调度账本已更新；你的设置未改变')
}

async function save() {
  if (!memValid.value || !cpuValid.value) return
  await act(async () => dataOf(await auth.api.PUT('/api/v1/agents/instances/{instance_id}/limits', { params: { header, path: { instance_id: props.instance.id } }, body: { mem_limit_mb: memValue.value, cpus: cpuValue.value, version: props.instance.version } })), '资源上限已保存：重新部署这个实例后生效')
}
</script>

<template>
  <section class="agent-section" aria-label="资源上限">
    <h3>资源上限：{{ summary }}</h3>
    <p class="form-help">一台服务器上有多个实例时，可以给这个实例的 OpenClaw 容器设内存和 CPU 上限，免得一个实例占满整台服务器。留空表示不限制；保存后重新部署这个实例才生效。</p>
    <form class="im-group-add" aria-label="资源上限" @submit.prevent="save">
      <label class="form-field"><span>内存上限（MiB，256 起）</span><input v-model.trim="mem" inputmode="numeric" placeholder="不限制" :aria-invalid="!memValid" /></label>
      <label class="form-field"><span>CPU 上限（核，可以是小数）</span><input v-model.trim="cpus" inputmode="decimal" placeholder="不限制" :aria-invalid="!cpuValid" /></label>
      <div class="form-actions"><button type="submit" class="secondary-button" :disabled="busy || !memValid || !cpuValid || !changed">保存上限</button></div>
    </form>
    <div v-if="auth.isOwner && instance.server_id" class="form-actions">
      <button type="button" class="secondary-button" :disabled="busy" @click="reconcile">核对已部署资源</button>
      <p class="form-help">只读取服务器现有容器的 CPU、内存上限，不修改容器或你设置的上限。实际未限制的资源不会按零占用计算。</p>
    </div>
    <p v-if="!memValid" class="feedback">内存上限是 256 到 1048576 之间的整数（MiB）。</p>
    <p v-if="!cpuValid" class="feedback">CPU 上限是 0.1 到 256 之间的数。</p>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
  </section>
</template>
