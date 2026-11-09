<script setup lang="ts">
import { CircleCheck, CircleX, Info } from '@lucide/vue'
import type { Schema } from '../../services/api'

// 本页“测试连接”的逐项结果（T-0091；T-0192 改成管理区面板）。只在用户点了测试连接（或保存后自动检查）之后出现；
// 各仓库的四项同时更新到“仓库权限核验”表。Webhook 不支持是 GitHub V1 的说明，不画成故障。
defineProps<{ result: Schema['IntegrationTestResult']; name: string }>()
defineEmits<{ close: [] }>()
const names = { reachable: '服务可达', version: '版本信息', token: 'Token', tasks_read: '任务读取', webhooks: 'Webhook', metadata: '仓库元数据', issues: 'Issues', pull_requests: 'Pull requests', actions: 'Actions' }
function reason(code?: string | null) {
  const reasons: Record<string, string> = { missing_permission: '缺少读取权限，请更新 Token 的仓库权限', not_found: '仓库不存在或 Token 未获授权', not_supported: 'GitHub V1 不支持 Webhook，使用周期同步', token_rejected: 'Token 已失效，请重新连接', api_version_unsupported: 'API 版本不兼容', rate_limited: 'GitHub 限流中，请按预计恢复时间重试', dns_failed: '无法解析服务地址' }
  return code ? ' · ' + code + '：' + (reasons[code] ?? '请核对服务地址、凭据及授权范围') : ''
}
// Webhook 不支持：只是说明（不是“未通过”）
const note = (check: { name: string; ok: boolean; code?: string | null }) => !check.ok && check.name === 'webhooks' && check.code === 'not_supported'
</script>
<template>
  <section class="ws-panel mg-section ig-checks" role="region" aria-label="连接检查结果">
    <div class="mg-section-head">
      <div class="mg-section-title">
        <h2>{{ name }} · {{ result.ok ? '必需检查通过' : '连接测试未通过' }}</h2>
        <p class="form-help">这次测试连接的逐项结果；各仓库的四项读取权限同时更新到下方的仓库权限核验。</p>
      </div>
      <div class="mg-section-actions"><button type="button" class="secondary-button" @click="$emit('close')">收起</button></div>
    </div>
    <p v-if="!result.compatible" class="mg-hint"><Info aria-hidden="true" />实例版本与核验版本不一致，请确认兼容性。</p>
    <ul class="ig-check-list">
      <li v-for="check in result.checks" :key="check.name" :class="note(check) ? 'tone-muted' : check.ok ? 'tone-green' : 'tone-red'">
        <Info v-if="note(check)" aria-hidden="true" /><CircleCheck v-else-if="check.ok" aria-hidden="true" /><CircleX v-else aria-hidden="true" />
        <span>{{ names[check.name] }}：{{ note(check) ? '不适用' : check.ok ? '通过' : '未通过' }}{{ reason(check.code) }}</span>
      </li>
    </ul>
    <div v-for="repository in result.repositories ?? []" :key="repository.full_name" class="ig-check-repo">
      <h3>{{ repository.full_name }}</h3>
      <p :class="repository.ok ? 'tone-green' : 'tone-red'">{{ repository.ok ? '仓库权限检查通过' : '仓库权限检查未通过' }}</p>
      <ul class="ig-check-list">
        <li v-for="check in repository.checks" :key="check.name" :class="check.ok ? 'tone-green' : 'tone-red'">
          <CircleCheck v-if="check.ok" aria-hidden="true" /><CircleX v-else aria-hidden="true" />
          <span>{{ names[check.name] }}：{{ check.ok ? '通过' : '未通过' }}{{ reason(check.code) }}</span>
        </li>
      </ul>
    </div>
    <p v-for="warning in result.warnings" :key="warning" class="ig-warning">{{ warning }}</p>
  </section>
</template>
