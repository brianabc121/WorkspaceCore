<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { Info } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { useConnectionModels, useConnectionOptions, type ModelChoice } from './modelBinding'

// 选择主模型（T-0207）：先选连接（中转站管理里启用、配置完整、协议已开放的），再选它的一个明确模型 ID。
// 新建智能体（instanceId 为 null）：列出全部可用连接，选择即授权给新实例；已有智能体：未授权的连接不可选，提示先授权。
// 只显示连接名称、类型、地址和模型 ID；不显示、也拿不到 Key。
const props = defineProps<{ instanceId: string | null; modelValue: ModelChoice | null; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: ModelChoice | null] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const ids = { connection: useId(), model: useId(), filter: useId() }

const options = useConnectionOptions(scope, () => props.instanceId)
const items = computed(() => options.data.value?.items ?? [])
const connectionId = ref(props.modelValue?.connection_id ?? '')
const modelId = ref(props.modelValue?.model_id ?? '')
const filter = ref('')
// 父组件预填或改写时同步；换连接时本组件先发出 null（模型要重选），这时保留刚选的连接，只清空模型
watch(() => props.modelValue, value => {
  if (value) {
    connectionId.value = value.connection_id
    modelId.value = value.model_id
  } else {
    modelId.value = ''
  }
})
const chosen = computed(() => items.value.find(o => o.id === connectionId.value) ?? null)
const models = useConnectionModels(scope, () => chosen.value?.id ?? null)
const loaded = computed(() => (models.data.value?.pages ?? []).flatMap(p => p.items))
const total = computed(() => models.data.value?.pages[0]?.total ?? 0)
const shown = computed(() => {
  const q = filter.value.trim().toLowerCase()
  return q ? loaded.value.filter(m => m.model_id.toLowerCase().includes(q) || m.display_name.toLowerCase().includes(q)) : loaded.value
})
const kind = (t: string) => t === 'official' ? '官方直连' : '第三方中转'

function pickConnection(value: string) {
  connectionId.value = value
  modelId.value = ''
  filter.value = ''
  emit('update:modelValue', null)
}
function pickModel(value: string) {
  modelId.value = value
  emit('update:modelValue', value && connectionId.value ? { connection_id: connectionId.value, model_id: value } : null)
}
</script>

<template>
  <div class="mc-picker">
    <div class="form-field">
      <label :for="ids.connection">模型连接 *</label>
      <select :id="ids.connection" :value="connectionId" :disabled="disabled || options.isPending.value" @change="pickConnection(($event.target as HTMLSelectElement).value)">
        <option value="" disabled>{{ options.isPending.value ? '正在加载连接…' : items.length ? '选择一条连接' : '没有可用的连接' }}</option>
        <option v-for="o in items" :key="o.id" :value="o.id" :disabled="!!instanceId && !o.granted">{{ o.name }} · {{ kind(o.connection_type) }}{{ instanceId && !o.granted ? '（未授权给这个智能体）' : '' }}</option>
      </select>
      <p v-if="options.isError.value" class="feedback" role="alert">连接列表加载失败，请稍后重试。</p>
      <p v-else-if="!options.isPending.value && !items.length" class="form-help">还没有启用且配置完整的连接：先到 <RouterLink to="/settings/model-connections">中转站管理</RouterLink> 添加连接（协议、地址、Key、模型）。</p>
      <p v-else-if="chosen" class="form-help">{{ chosen.base_url }} · {{ chosen.model_count }} 个模型</p>
      <p v-if="!instanceId" class="form-help ag-hint"><Info aria-hidden="true" />选择即把这条连接授权给新智能体（与创建在同一步完成）。</p>
      <p v-else-if="items.some(o => !o.granted)" class="form-help ag-hint"><Info aria-hidden="true" />未授权的连接要先在中转站管理的“智能体授权”里授权给这个智能体。</p>
    </div>
    <div v-if="chosen" class="form-field">
      <label :for="ids.model">主模型 *</label>
      <input v-if="total > 20" :id="ids.filter" v-model.trim="filter" class="mc-filter" type="search" placeholder="按模型 ID 筛选已加载的模型" aria-label="筛选模型" :disabled="disabled" />
      <select :id="ids.model" :value="modelId" :disabled="disabled || models.isPending.value" @change="pickModel(($event.target as HTMLSelectElement).value)">
        <option value="" disabled>{{ models.isPending.value ? '正在加载模型…' : shown.length ? '选择一个模型' : '没有匹配的模型' }}</option>
        <option v-for="m in shown" :key="m.id" :value="m.model_id">{{ m.model_id }}{{ m.display_name && m.display_name !== m.model_id ? `（${m.display_name}）` : '' }}</option>
      </select>
      <p v-if="models.isError.value" class="feedback" role="alert">模型列表加载失败，请稍后重试。</p>
      <p class="form-help">
        模型 ID 原样发给服务商。已加载 {{ loaded.length }} / {{ total }} 个
        <button v-if="models.hasNextPage.value" type="button" class="link-button" :disabled="models.isFetchingNextPage.value" @click="models.fetchNextPage()">加载更多</button>
      </p>
    </div>
  </div>
</template>
