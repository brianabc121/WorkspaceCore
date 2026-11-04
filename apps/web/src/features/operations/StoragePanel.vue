<script setup lang="ts">
import { computed, ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, errorMessage, type Schema } from '../../services/api'

// 知识库图片存储（T-0103，owner / admin）：主存储、副本、最近一次自检、用量与待复制数量
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, header = { 'X-Workspace-Id': scope }
const cache = useQueryClient()
const key = ['operations', user, scope, 'storage']
const status = useQuery({ queryKey: key, queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/operations/storage', { params: { header }, signal })) })
const checking = ref(false), failure = ref('')
async function check() {
  checking.value = true; failure.value = ''
  try { cache.setQueryData(key, dataOf(await auth.api.POST('/api/v1/operations/storage/check', { params: { header } }))) }
  catch (error) { failure.value = errorMessage(error) }
  finally { checking.value = false }
}
const NAMES: Record<Schema['StorageLocation']['name'], string> = { local: '本机（卷 kb-files）', r2: 'Cloudflare R2', oss: '阿里云 OSS' }
const size = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`
const usage = computed(() => status.data.value ? Math.min(100, Math.round((status.data.value.used_bytes / status.data.value.quota_bytes) * 100)) : 0)
const when = (iso: string) => new Date(iso).toLocaleString('zh-CN', { hour12: false })
function state(location: Schema['StorageLocation']) {
  if (!location.configured) return { tone: 'is-bad', text: `没配置完整：缺少 ${(location.missing ?? []).join('、')}` }
  if (!location.check) return { tone: '', text: '还没有自检过' }
  return location.check.ok
    ? { tone: 'is-good', text: `自检通过（${when(location.check.checked_at)}）` }
    : { tone: 'is-bad', text: `自检失败：${location.check.reason ?? '未知原因'}（${when(location.check.checked_at)}）` }
}
</script>

<template>
  <section class="panel form-panel" aria-label="知识库图片存储">
    <div class="section-heading">
      <h2>知识库图片存储</h2>
      <button class="secondary-button" :disabled="checking || status.isPending.value" @click="check">{{ checking ? '正在自检…' : '自检（写、读、删一个探测文件）' }}</button>
    </div>
    <p v-if="status.isPending.value" class="form-help" role="status">正在加载…</p>
    <p v-else-if="status.error.value" class="feedback" role="alert">{{ errorMessage(status.error.value) }}</p>
    <template v-else-if="status.data.value">
      <dl class="storage-grid">
        <dt>主存储</dt>
        <dd>
          <strong>{{ NAMES[status.data.value.primary.name] }}</strong><span v-if="status.data.value.primary.where" class="form-help"> · {{ status.data.value.primary.where }}</span>
          <span class="storage-state" :class="state(status.data.value.primary).tone">{{ state(status.data.value.primary).text }}</span>
        </dd>
        <dt>副本</dt>
        <dd v-if="status.data.value.replica">
          <strong>{{ NAMES[status.data.value.replica.name] }}</strong><span v-if="status.data.value.replica.where" class="form-help"> · {{ status.data.value.replica.where }}</span>
          <span class="storage-state" :class="state(status.data.value.replica).tone">{{ state(status.data.value.replica).text }}</span>
          <span class="form-help">待复制 {{ status.data.value.pending_replication }} 张<template v-if="status.data.value.failed_replication"> · 复制失败 {{ status.data.value.failed_replication }} 张</template></span>
        </dd>
        <dd v-else class="form-help">没有配置副本（FILE_STORAGE_REPLICA）</dd>
        <dt>本工作空间</dt>
        <dd>
          <span>{{ status.data.value.files }} 张图片，已用 {{ size(status.data.value.used_bytes) }} / {{ size(status.data.value.quota_bytes) }}</span>
          <span class="storage-meter" role="meter" :aria-valuenow="usage" aria-valuemin="0" aria-valuemax="100" :aria-label="`图片配额已用 ${usage}%`"><span :style="{ width: `${usage}%` }" /></span>
        </dd>
      </dl>
      <p v-if="failure" class="feedback" role="alert">{{ failure }}</p>
      <p class="form-help">配置方法：部署脚本 <code>deploy.sh connect</code> 的“图片存储”一步，或在 <code>.env</code> 里设置 FILE_STORAGE / FILE_STORAGE_REPLICA 与 R2_* / OSS_*。没有笔记引用的图片上传 24 小时后自动清理。</p>
    </template>
  </section>
</template>

<style scoped>
.storage-grid { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 10px 16px; margin: 0; }
.storage-grid dt { color: var(--muted); }
.storage-grid dd { margin: 0; display: flex; flex-direction: column; gap: 4px; min-width: 0; overflow-wrap: anywhere; }
.storage-state { font-size: 13px; }
.storage-state.is-good { color: var(--success); }
.storage-state.is-bad { color: var(--danger); }
.storage-meter { display: block; height: 6px; max-width: 320px; border-radius: 3px; background: var(--line); overflow: hidden; }
.storage-meter > span { display: block; height: 100%; background: var(--accent); }
</style>
