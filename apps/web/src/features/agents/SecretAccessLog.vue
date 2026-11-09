<script setup lang="ts">
import { computed, reactive } from 'vue'
import { X } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import ManagementSection from '../../components/management/ManagementSection.vue'
import { unique, useInstances, useSecretAccesses } from './queries'
import { ACCESS_RESULT, secretError } from './secrets'
import SecretAccessTable from './SecretAccessTable.vue'

// “取用记录”标签（T-0196）：复用 GET /agents/secrets/accesses 的服务端筛选（密钥 / 智能体 / 结果）与分页，
// 条件进查询键，换条件从第一页开始。“未授权或不存在”不区分两种情况；明文从不记录。
const props = defineProps<{ secret: { id: string; name: string } | null }>()
const emit = defineEmits<{ 'clear-secret': [] }>()
const scope = useAuthStore().workspaceId!
const instances = useInstances(scope)
const instanceItems = computed(() => unique(instances.data.value?.pages))
const filter = reactive<{ instance_id: string; result: '' | Schema['SecretAccessOut']['result'] }>({ instance_id: '', result: '' })
const accesses = useSecretAccesses(scope, () => ({
  ...(props.secret ? { secret_id: props.secret.id } : {}),
  ...(filter.instance_id ? { instance_id: filter.instance_id } : {}),
  ...(filter.result ? { result: filter.result } : {}),
}))
const items = computed(() => unique(accesses.data.value?.pages))
const filtered = computed(() => !!(props.secret || filter.instance_id || filter.result))
</script>

<template>
  <ManagementSection title="取用记录" description="智能体取用密钥的记录（最近的在前），明文从不记录。“未授权或不存在”不区分是没有授权还是密钥不存在。">
    <div class="ws-toolbar sk-access-filters">
      <span v-if="secret" class="mg-tag sk-chip">密钥：{{ secret.name }}<button type="button" class="sk-chip-clear" :aria-label="`不再按密钥「${secret.name}」筛选`" @click="emit('clear-secret')"><X aria-hidden="true" /></button></span>
      <label class="form-field"><span>智能体</span><select v-model="filter.instance_id"><option value="">全部智能体</option><option v-for="i in instanceItems" :key="i.id" :value="i.id">{{ i.name }}</option></select></label>
      <label class="form-field"><span>结果</span><select v-model="filter.result"><option value="">全部结果</option><option v-for="(label, value) in ACCESS_RESULT" :key="value" :value="value">{{ label }}</option></select></label>
    </div>
    <p v-if="accesses.isPending.value" role="status" class="ws-state-line">正在加载取用记录…</p>
    <div v-else-if="accesses.error.value && !items.length" class="ws-state-line"><p role="alert">{{ secretError(accesses.error.value) }}</p><button type="button" class="secondary-button" @click="accesses.refetch()">重试</button></div>
    <p v-else-if="!items.length" class="ws-state-line">{{ filtered ? '没有符合条件的取用记录。' : '还没有取用记录。' }}</p>
    <SecretAccessTable v-else :items="items" />
    <footer v-if="items.length" class="asv-foot ag-foot">
      <span>已加载 {{ items.length }} 条<template v-if="accesses.error.value"> · 刷新失败，显示的是上次取回的记录</template></span>
      <button v-if="accesses.hasNextPage.value" type="button" class="secondary-button" :disabled="accesses.isFetchingNextPage.value" @click="accesses.fetchNextPage()">加载更多记录</button>
    </footer>
  </ManagementSection>
</template>
