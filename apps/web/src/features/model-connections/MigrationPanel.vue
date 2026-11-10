<script setup lang="ts">
import { computed, ref } from 'vue'
import { KeyRound } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'
import ManagementSection from '../../components/management/ManagementSection.vue'
import { useAgentAction } from '../agents/action'
import { LEGACY_KIND, MIGRATION_LABEL, mcError, useMigrationEntries, type MigrationEntry, type MigrationState } from './api'

// 迁移记录（契约第 2 节“迁移分类与兼容凭据状态”）：旧密钥库导入的项按状态列出。
// 待完善：打开详情补全 URL、协议与模型（只存元数据，不改旧运行时、不自动建绑定）；待归类：归为模型连接或非模型凭据；
// 非模型凭据仍在下方“兼容凭据”里管理（轮换、删除、取用记录），也可以改回模型连接。归类保留原授权与凭据，不恢复已撤销的访问。
const emit = defineEmits<{ open: [id: string] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const STATES: { key: MigrationState | null; name: string }[] = [
  { key: null, name: '全部' }, { key: 'needs_configuration', name: '待完善' }, { key: 'needs_classification', name: '待归类' },
  { key: 'needs_review', name: '需复核' }, { key: 'managed', name: '已纳管' }, { key: 'non_model', name: '非模型凭据' },
]
const state = ref<MigrationState | null>(null)
const entries = useMigrationEntries(scope, () => state.value)
const items = computed(() => [...new Map((entries.data.value?.pages ?? []).flatMap(p => p.items).map(e => [e.entry_id, e])).values()])
const counts = computed(() => entries.data.value?.pages[0]?.counts ?? {})
const total = computed(() => entries.data.value?.pages[0]?.total)
const all = computed(() => ['needs_configuration', 'needs_classification', 'needs_review', 'managed', 'non_model'].reduce((n, k) => n + (counts.value[k] ?? 0), 0))
const { busy, error, notice, act, header } = useAgentAction(scope, mcError)
const TONE: Record<string, string> = { needs_configuration: 'amber', needs_classification: 'amber', needs_review: 'red', managed: 'green', non_model: 'muted', native: 'muted' }

async function classify(entry: MigrationEntry, purpose: 'model' | 'non_model') {
  const name = entry.legacy_name
  await act(async () => dataOf(await auth.api.POST('/api/v1/model-connections/migration/entries/{entry_id}/classify', { params: { header, path: { entry_id: entry.entry_id } }, body: { version: entry.connection.version, purpose } })),
    purpose === 'model' ? `「${name}」已归为模型连接：补全 URL、协议与模型后才能给智能体选用。` : `「${name}」已归为非模型凭据：仍可在下方“兼容凭据”里管理，原授权保留。`)
}
</script>

<template>
  <ManagementSection title="迁移记录" description="旧密钥库导入的凭据。补全配置只保存连接信息，不改变旧智能体的运行配置，也不会自动给智能体换模型。">
    <template #title>迁移记录<template v-if="typeof total === 'number'"> · {{ total }}</template></template>
    <div class="ws-chips mc-states-filter" role="group" aria-label="按迁移状态筛选">
      <button v-for="s in STATES" :key="s.key ?? 'all'" type="button" class="ws-chip" :aria-pressed="state === s.key" @click="state = s.key">{{ s.name }}<span class="ws-chip-count">{{ s.key ? counts[s.key] ?? 0 : all }}</span></button>
    </div>
    <p v-if="counts.unmapped" class="form-help">还有 {{ counts.unmapped }} 个旧密钥没有导入（由管理员在服务器上运行迁移命令）。</p>
    <p v-if="entries.isPending.value" role="status" class="ws-state-line">正在加载迁移记录…</p>
    <div v-else-if="entries.error.value && !items.length" class="ws-state-line"><p role="alert">{{ mcError(entries.error.value) }}</p><button type="button" class="secondary-button" @click="entries.refetch()">重试</button></div>
    <p v-else-if="!items.length" class="ws-state-line">{{ state ? '没有这个状态的导入项。' : '没有从旧密钥库导入的凭据。' }}</p>
    <div v-else class="ws-table-wrap">
      <table class="ws-table asv-table mc-migration">
        <thead><tr><th scope="col">旧密钥</th><th scope="col">原授权引用</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead>
        <tbody>
          <tr v-for="e in items" :key="e.entry_id" :data-entry="e.entry_id">
            <th scope="row" data-label="旧密钥"><span class="asv-name"><KeyRound aria-hidden="true" /><span>{{ e.legacy_name }}</span></span><span class="asv-sub">{{ LEGACY_KIND[e.legacy_kind] }}<template v-if="e.connection.base_url"> · {{ e.connection.base_url }}</template></span></th>
            <td data-label="原授权引用">{{ e.legacy_grant_refs.length ? e.legacy_grant_refs.join('、') : '未授权' }}</td>
            <td data-label="状态"><span class="asv-state" :class="`tone-${TONE[e.connection.migration_state] ?? 'muted'}`">{{ e.connection.configuration_state === 'needs_review' ? '需复核' : MIGRATION_LABEL[e.connection.migration_state] }}</span></td>
            <td class="asv-link-cell" data-label="操作">
              <span class="mc-entry-actions">
                <template v-if="e.connection.migration_state === 'needs_classification'">
                  <button type="button" class="asv-link" :disabled="busy" :aria-label="`归为模型连接：${e.legacy_name}`" @click="classify(e, 'model')">归为模型连接</button>
                  <button type="button" class="asv-link" :disabled="busy" :aria-label="`归为非模型凭据：${e.legacy_name}`" @click="classify(e, 'non_model')">归为非模型凭据</button>
                </template>
                <button v-else-if="e.connection.migration_state === 'non_model'" type="button" class="asv-link" :disabled="busy" :aria-label="`改回模型连接：${e.legacy_name}`" @click="classify(e, 'model')">改回模型连接</button>
                <button v-else type="button" class="asv-link" :aria-label="`${e.connection.migration_state === 'needs_configuration' ? '补全配置' : '查看'}：${e.legacy_name}`" @click="emit('open', e.connection.id)">{{ e.connection.migration_state === 'needs_configuration' ? '补全配置' : '查看' }}</button>
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <footer v-if="items.length" class="asv-foot ag-foot">
      <span>已显示 {{ items.length }} 项<template v-if="typeof total === 'number'">，共 {{ total }} 项</template></span>
      <button v-if="entries.hasNextPage.value" type="button" class="secondary-button" :disabled="entries.isFetchingNextPage.value" @click="entries.fetchNextPage()">加载更多</button>
    </footer>
    <p v-if="error" class="feedback" role="alert">{{ error }}</p>
    <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  </ManagementSection>
</template>
