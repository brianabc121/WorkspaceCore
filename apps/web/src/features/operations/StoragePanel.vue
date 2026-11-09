<script setup lang="ts">
import { computed, ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { Database } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf, errorMessage, type Schema } from '../../services/api'

// 存储状态（T-0103 知识库图片存储，owner / admin；T-0177 改版）：主存储、副本、最近一次自检、用量与待复制数量。
// “执行存储读写自检”会真的写入、读取、删除一个探测对象：只在有权限的人明确点击后调用一次，
// 进入页面、刷新、切换时间范围、定时刷新都不会调用。没有副本时写“未配置副本”，不当成正常。
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, header = { 'X-Workspace-Id': scope }
const cache = useQueryClient()
const key = ['operations', user, scope, 'storage']
const status = useQuery({ queryKey: key, queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/operations/storage', { params: { header }, signal })) })
const checking = ref(false), failure = ref('')
async function check() {
  if (checking.value) return
  checking.value = true; failure.value = ''
  try { cache.setQueryData(key, dataOf(await auth.api.POST('/api/v1/operations/storage/check', { params: { header } }))) }
  catch (error) { failure.value = errorMessage(error) }
  finally { checking.value = false }
}
const NAMES: Record<Schema['StorageLocation']['name'], string> = { local: '本机（卷 kb-files）', r2: 'Cloudflare R2', oss: '阿里云 OSS' }
const size = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`
const usage = computed(() => status.data.value && status.data.value.quota_bytes > 0 ? Math.min(100, Math.round((status.data.value.used_bytes / status.data.value.quota_bytes) * 100)) : 0)
const when = (iso: string) => new Date(iso).toLocaleString('zh-CN', { hour12: false })
function state(location: Schema['StorageLocation']) {
  if (!location.configured) return { tone: 'is-bad', text: `没配置完整：缺少 ${(location.missing ?? []).join('、')}` }
  if (!location.check) return { tone: 'is-muted', text: '还没有自检过' }
  return location.check.ok
    ? { tone: 'is-good', text: `自检通过（${when(location.check.checked_at)}）` }
    : { tone: 'is-bad', text: `自检失败：${location.check.reason ?? '未知原因'}（${when(location.check.checked_at)}）` }
}
</script>

<template>
  <section class="ws-panel ops-storage" aria-label="存储状态">
    <div class="ops-panel-head">
      <h2><Database aria-hidden="true" />存储状态</h2>
      <span class="ops-muted">知识库图片存储</span>
    </div>
    <p v-if="status.isPending.value" class="ops-muted" role="status">正在加载…</p>
    <p v-else-if="status.error.value" class="feedback" role="alert">{{ errorMessage(status.error.value) }}</p>
    <template v-else-if="status.data.value">
      <dl class="ops-storage-grid">
        <dt>主存储</dt>
        <dd>
          <strong>{{ NAMES[status.data.value.primary.name] }}</strong><span v-if="status.data.value.primary.where" class="ops-muted"> · {{ status.data.value.primary.where }}</span>
          <span class="ops-storage-state" :class="state(status.data.value.primary).tone">{{ state(status.data.value.primary).text }}</span>
        </dd>
        <dt>副本</dt>
        <dd v-if="status.data.value.replica">
          <strong>{{ NAMES[status.data.value.replica.name] }}</strong><span v-if="status.data.value.replica.where" class="ops-muted"> · {{ status.data.value.replica.where }}</span>
          <span class="ops-storage-state" :class="state(status.data.value.replica).tone">{{ state(status.data.value.replica).text }}</span>
          <span class="ops-muted">待复制 {{ status.data.value.pending_replication }} 张<template v-if="status.data.value.failed_replication"> · 复制失败 {{ status.data.value.failed_replication }} 张</template></span>
        </dd>
        <dd v-else>
          <span class="ops-storage-state is-muted">未配置副本</span>
          <span class="ops-muted">没有设置 FILE_STORAGE_REPLICA：图片只存在主存储里。</span>
        </dd>
        <dt>本工作空间</dt>
        <dd>
          <span>{{ status.data.value.files }} 张图片，已用 {{ size(status.data.value.used_bytes) }} / {{ size(status.data.value.quota_bytes) }}</span>
          <span class="ops-meter" role="meter" :aria-valuenow="usage" aria-valuemin="0" aria-valuemax="100" :aria-label="`图片配额已用 ${usage}%`"><span :style="{ width: `${usage}%` }" /></span>
        </dd>
      </dl>
      <div class="ops-storage-check">
        <button type="button" class="secondary-button" :disabled="checking" @click="check">{{ checking ? '正在自检…' : '执行存储读写自检' }}</button>
        <p class="ops-muted">会在主存储（和副本）里写入、读取并删除一个探测对象，用来确认存储可用。进入页面和刷新都不会执行。</p>
      </div>
      <p v-if="failure" class="feedback" role="alert">{{ failure }}</p>
      <p class="ops-muted ops-storage-help">配置方法：部署脚本 <code>deploy.sh connect</code> 的“图片存储”一步，或在 <code>.env</code> 里设置 FILE_STORAGE / FILE_STORAGE_REPLICA 与 R2_* / OSS_*。没有笔记引用的图片上传 24 小时后自动清理。</p>
    </template>
  </section>
</template>
