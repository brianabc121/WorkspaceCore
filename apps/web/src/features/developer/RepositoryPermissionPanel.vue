<script setup lang="ts">
import { computed } from 'vue'
import { CircleCheck, CircleHelp, CircleX, Settings } from '@lucide/vue'
import { ApiError, errorMessage, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { PERMISSION_ROWS, checkCode } from './format'

// 所选仓库的四项读取权限（T-0176）：只显示管理员上次“测试连接”时实际得到的结果与核验时间。
// 没有结果的项一律“未核验”，不从仓库的 access=ok 推断成通过；这里不发起测试连接。
const props = defineProps<{
  repo: Schema['RepoOut'] | null
  evidence?: Schema['RepositoryPermissionEvidenceOut']
  pending: boolean
  error: unknown
  canManage: boolean
}>()
defineEmits<{ retry: [] }>()
// 只用属于当前所选仓库的结果：切换仓库时旧仓库的结果不会显示在新仓库下
const current = computed(() => (props.repo && props.evidence?.repo_id === props.repo.id ? props.evidence : undefined))
const rows = computed(() => PERMISSION_ROWS.map((row) => {
  const check = current.value?.checks.find((c) => c.name === row.name)
  if (!check) return { ...row, state: 'unknown' as const, text: '未核验', detail: '' }
  return check.ok ? { ...row, state: 'ok' as const, text: '通过', detail: '' } : { ...row, state: 'failed' as const, text: '未通过', detail: checkCode(check.code) }
}))
const gone = computed(() => props.error instanceof ApiError && props.error.status === 404)
</script>

<template>
  <section class="ws-panel dv-panel dv-permissions" aria-labelledby="dv-permissions-title">
    <div class="dv-panel-head">
      <h2 id="dv-permissions-title">{{ repo ? `所选仓库：${repo.full_name} 的读取权限` : '所选仓库的读取权限' }}</h2>
    </div>
    <p v-if="!repo" class="ws-state-line">在仓库概览里选择一个仓库。</p>
    <p v-else-if="pending" role="status" class="ws-state-line">正在读取权限核验结果…</p>
    <p v-else-if="gone" class="ws-state-line">这个仓库已不在当前的仓库清单里。</p>
    <div v-else-if="error" class="dv-error">
      <p role="alert">权限核验结果读取失败：{{ errorMessage(error) }}</p>
      <button type="button" class="secondary-button" @click="$emit('retry')">重试</button>
    </div>
    <template v-else>
      <ul class="dv-checks" aria-label="四项读取权限">
        <li v-for="row in rows" :key="row.name" :class="`is-${row.state}`">
          <span class="dv-check-name">{{ row.label }}</span>
          <span class="ws-state" :class="row.state === 'ok' ? 'dv-tone-success' : row.state === 'failed' ? 'dv-tone-danger' : 'dv-tone-muted'">
            <CircleCheck v-if="row.state === 'ok'" aria-hidden="true" /><CircleX v-else-if="row.state === 'failed'" aria-hidden="true" /><CircleHelp v-else aria-hidden="true" />{{ row.text }}
          </span>
          <span v-if="row.detail" class="dv-check-detail">{{ row.detail }}</span>
        </li>
      </ul>
      <p v-if="current?.checked_at && !current.complete" class="dv-note">有的项目这次没有执行（例如仓库元数据未通过时其余项不检查，或测试中途被限流），显示为“未核验”。</p>
      <div class="dv-permissions-foot">
        <RouterLink v-if="canManage" to="/settings/integrations" class="dv-link"><Settings aria-hidden="true" />前往连接设置</RouterLink>
        <span v-else class="ws-meta">由管理员在连接设置中测试连接后更新</span>
        <span class="ws-meta">
          <template v-if="current?.checked_at">上次核验 <time :datetime="current.checked_at">{{ formatTime(current.checked_at) }}</time></template>
          <template v-else>尚未核验</template>
        </span>
      </div>
      <p class="dv-note">结果来自上次测试连接，不是实时权限。</p>
    </template>
  </section>
</template>
