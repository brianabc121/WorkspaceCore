<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'
import { useAgentAction } from '../agents/action'
import { formatTime } from '../time'
import { SCOPE_LABEL, isCode, mcError, modelsInUse, useAllInstances, useAllModels, useModels, type Connection, type ConnectionModel } from './api'

// 模型管理（契约第 1 节“模型记录”）：明确的模型 ID，独立分页；source = 手工 / 同步，availability = 已列出 / 推理验证过。
// “指定模型”范围下可以移除单个模型：按完整集合整体替换（PUT models）；正被智能体使用的模型返回 409 MODEL_IN_USE，列出这些智能体。
const props = defineProps<{ connection: Connection }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const page = useModels(scope, () => props.connection.id)
const all = useAllModels(scope, () => props.connection.id)
const items = computed(() => [...new Map((page.data.value?.pages ?? []).flatMap(p => p.items).map(m => [m.id, m])).values()])
const first = computed(() => page.data.value?.pages[0] ?? null)
const SYNC = { complete: '完整', partial: '不完整（保留了上一次的完整列表）', unsupported: '服务商不支持列出模型', failed: '失败' } as const
const { busy, error, notice, failure, act, header } = useAgentAction(scope, mcError)
const blocked = ref<{ model: string; ids: string[] }[]>([])
const instances = useAllInstances(scope, () => blocked.value.length > 0)
const name = (id: string) => instances.data.value?.find(i => i.id === id)?.name ?? id

async function remove(model: ConnectionModel) {
  const c = props.connection
  const full = all.data.value?.items
  if (!full || busy.value) return
  if (!window.confirm(`从「${c.name}」移除模型 ${model.model_id}？`)) return
  blocked.value = []
  const models = full.filter(m => m.model_id !== model.model_id).map(m => ({ model_id: m.model_id, display_name: m.display_name || null }))
  const ok = await act(() => auth.api.PUT('/api/v1/model-connections/{connection_id}/models', { params: { header, path: { connection_id: c.id } }, body: { version: c.version, model_scope: 'selected', models } }).then(dataOf), `已移除 ${model.model_id}。`)
  if (!ok && isCode(failure.value, 'MODEL_IN_USE')) blocked.value = Object.entries(modelsInUse(failure.value)).map(([m, ids]) => ({ model: m, ids }))
}
</script>

<template>
  <section class="mc-tabpanel" aria-label="模型管理">
    <p class="form-help">
      {{ SCOPE_LABEL[connection.model_scope] }} · 共 {{ first?.total ?? connection.model_count }} 个模型
      <template v-if="first?.synced_at"> · 最近同步 <time :datetime="first.synced_at">{{ formatTime(first.synced_at) }}</time>（{{ SYNC[first.sync_state ?? 'failed'] }}）</template>
      <template v-else> · 还没有同步过</template>
    </p>
    <p class="form-help">“已列出”只表示服务商列表里有或手工填写，不代表一定能推理；推理检测通过的模型标为“已验证”。修改范围或同步在“连接配置”里。</p>
    <p v-if="page.isPending.value" role="status" class="ws-state-line">正在加载模型…</p>
    <div v-else-if="page.error.value && !items.length" class="ws-state-line"><p role="alert">{{ mcError(page.error.value) }}</p><button type="button" class="secondary-button" @click="page.refetch()">重试</button></div>
    <p v-else-if="!items.length" class="ws-state-line">还没有模型。</p>
    <div v-else class="ws-table-wrap">
      <table class="ws-table asv-table mc-models">
        <thead><tr><th scope="col">模型 ID</th><th scope="col">来源</th><th scope="col">可用性</th><th scope="col">最近出现</th><th v-if="connection.model_scope === 'selected'" scope="col"><span class="sr-only">操作</span></th></tr></thead>
        <tbody>
          <tr v-for="m in items" :key="m.id">
            <th scope="row" data-label="模型 ID"><code>{{ m.model_id }}</code><span v-if="m.display_name && m.display_name !== m.model_id" class="asv-sub">{{ m.display_name }}</span></th>
            <td data-label="来源">{{ m.source === 'manual' ? '手工' : '同步' }}</td>
            <td data-label="可用性"><span class="asv-state" :class="m.availability === 'verified' ? 'tone-green' : 'tone-muted'">{{ m.availability === 'verified' ? '已验证' : '已列出' }}</span></td>
            <td data-label="最近出现" class="asv-nowrap">{{ m.last_seen_at ? formatTime(m.last_seen_at) : '—' }}</td>
            <td v-if="connection.model_scope === 'selected'" class="asv-link-cell"><button type="button" class="asv-link" :disabled="busy || !all.data.value || items.length < 2 && (first?.total ?? 0) < 2" :aria-label="`移除模型 ${m.model_id}`" @click="remove(m)">移除</button></td>
          </tr>
        </tbody>
      </table>
    </div>
    <footer v-if="items.length" class="asv-foot ag-foot">
      <span>已显示 {{ items.length }} 个<template v-if="first">，共 {{ first.total }} 个</template></span>
      <button v-if="page.hasNextPage.value" type="button" class="secondary-button" :disabled="page.isFetchingNextPage.value" @click="page.fetchNextPage()">加载更多模型</button>
    </footer>
    <div v-if="blocked.length" class="mc-callout" role="alert">
      <p><strong>模型正被智能体使用，没有移除</strong></p>
      <ul><li v-for="b in blocked" :key="b.model"><code>{{ b.model }}</code>：{{ b.ids.map(name).join('、') }}</li></ul>
      <p>请先在“OpenClaw 连接分配”里给这些智能体换模型。</p>
    </div>
    <p v-else-if="error" class="feedback" role="alert">{{ error }}</p>
    <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  </section>
</template>
