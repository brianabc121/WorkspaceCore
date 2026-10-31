<script setup lang="ts">
import type { Schema } from '../../services/api'
defineProps<{ result: Schema['IntegrationTestResult']; name: string }>()
const names = { reachable: '服务可达', version: '版本信息', token: 'Token', tasks_read: '任务读取', webhooks: 'Webhook', metadata: '仓库元数据', issues: 'Issues', pull_requests: 'Pull requests', actions: 'Actions' }
function reason(code?: string | null) {
  const reasons: Record<string, string> = { missing_permission: '缺少读取权限，请更新 Token 的仓库权限', not_found: '仓库不存在或 Token 未获授权', not_supported: '不支持此可选功能', token_rejected: 'Token 已失效，请重新连接', api_version_unsupported: 'API 版本不兼容', rate_limited: 'GitHub 限流中，请按预计恢复时间重试', dns_failed: '无法解析服务地址' }
  return code ? ' · ' + code + '：' + (reasons[code] ?? '请核对服务地址、凭据及授权范围') : ''
}
</script>
<template><section class="panel form-panel" role="region" aria-label="连接检查结果"><h2>{{ name }} · {{ result.ok ? '必需检查通过' : '连接测试未通过' }}</h2><p v-if="!result.compatible">实例版本与核验版本不一致，请确认兼容性。</p><ul><li v-for="check in result.checks" :key="check.name">{{ names[check.name] }}：{{ check.ok ? '通过' : '未通过' }}{{ reason(check.code) }}</li></ul><section v-for="repository in result.repositories ?? []" :key="repository.full_name"><h3>{{ repository.full_name }}</h3><p>{{ repository.ok ? '仓库权限检查通过' : '仓库权限检查未通过' }}</p><ul><li v-for="check in repository.checks" :key="check.name">{{ names[check.name] }}：{{ check.ok ? '通过' : '未通过' }}{{ reason(check.code) }}</li></ul></section><p v-for="warning in result.warnings" :key="warning">{{ warning }}</p></section></template>
