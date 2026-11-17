<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, Recycle } from '@lucide/vue'
import { dataOf } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { POLICY_FIELDS, useRunnerAction, type RunnerHost } from './api'

// 回收策略（RUNNER_PLAN 第 9、11 节；T-0277 起在服务器详情里，默认折叠）：各类别时长、水位、快照上限。
// 显示生效值，只提交改过的（清空的跟着默认值走）；保存带版本号，防止两个人同时改。
const props = defineProps<{ host: RunnerHost }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const { busy, error, notice, act, header } = useRunnerAction(scope)
const open = ref(false)
const values = ref<Record<string, string>>({})
const reset = () => { values.value = Object.fromEntries(POLICY_FIELDS.map(f => [f.key, String(props.host.policy[f.key] ?? '')])) }
watch(() => props.host.version, reset, { immediate: true })
const groups = computed(() => [...new Set(POLICY_FIELDS.map(f => f.group))].map(group => ({ group, fields: POLICY_FIELDS.filter(f => f.group === group) })))
const pct = (key: string) => {
  const v = props.host.policy[key]
  return typeof v === 'number' ? `${Math.round(v * 100)}%` : '默认'
}
const summary = computed(() => `会话空闲 ${props.host.policy.session_idle_minutes ?? '默认'} 分钟停止 · 内存高水位 ${pct('mem_high')} · 磁盘高水位 ${pct('disk_high')}`)

async function save() {
  const policy: Record<string, number> = {}
  for (const field of POLICY_FIELDS) {
    const raw = String(values.value[field.key] ?? '').trim()
    if (!raw) continue
    const value = Number(raw)
    if (!Number.isFinite(value) || value < 0) { error.value = `${field.label} 要填不小于 0 的数字`; return }
    if (field.step === 0.01 && value > 1) { error.value = `${field.label} 要在 0 到 1 之间`; return }
    policy[field.key] = value
  }
  await act(async () => {
    dataOf(await auth.api.PUT('/api/v1/runner/hosts/{host_id}/policy', { params: { header, path: { host_id: props.host.id } }, body: { policy, version: props.host.version } }))
  }, '已保存回收策略：下一轮检查（每分钟）起生效')
}
</script>

<template>
  <section class="ws-panel asv-section cs-policy" aria-label="回收策略">
    <div class="asv-panel-head">
      <h3><Recycle aria-hidden="true" />回收策略</h3>
      <span class="asv-sub">{{ summary }}</span>
      <button type="button" class="secondary-button cs-policy-toggle" :aria-expanded="open" aria-controls="cs-policy-form" @click="open = !open">
        <component :is="open ? ChevronDown : ChevronRight" aria-hidden="true" />{{ open ? '收起' : '查看与修改' }}
      </button>
    </div>
    <form v-show="open" id="cs-policy-form" class="runner-form" aria-label="回收策略" @submit.prevent="save">
      <p class="form-help">显示的是生效值；清空一项就回到默认值。启动越慢停得越晚：空闲停止的时长取“类别默认”和“启动耗时 × 倍数”中大的那个。</p>
      <fieldset v-for="g in groups" :key="g.group" class="mail-scope">
        <legend>{{ g.group }}</legend>
        <div class="runner-filters">
          <label v-for="f in g.fields" :key="f.key" class="form-field"><span>{{ f.label }}</span><input v-model="values[f.key]" type="number" min="0" :step="f.step ?? 1" /></label>
        </div>
      </fieldset>
      <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy">保存回收策略</button><button type="button" class="secondary-button" @click="reset">还原</button></div>
    </form>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
  </section>
</template>
