// 智能体服务器与 OpenClaw 智能体两页（T-0181 / T-0182）测试共用的数据：字段与 schema.d.ts 一致，数字、名称、地址都是示例。
import type { Schema } from '../src/services/api'

export const at = '2026-11-09T04:00:00Z'
export const GB = 1024 ** 3

export const instance = (over: Partial<Schema['AgentInstanceOut']> = {}): Schema['AgentInstanceOut'] => ({
  id: 'inst-a', name: 'alpha', description: '', network_mode: 'public', scope: 'read', max_role: 'member', status: 'pending', im_user_id: 'oc_a', im_nickname: '甲', im_ready: true, acting_user_id: 'user-a',
  last_seen_at: null, reported_version: null, plugin_version: null, bundle_issued_at: null, im_token_expires_at: null, disabled_at: null,
  project_admin: { enabled: false, granted_by: null, granted_at: null }, im_media: { enabled: false, granted_by: null, granted_at: null }, stop_others: { enabled: false, granted_by: null, granted_at: null },
  mail: { accounts: [], read_enabled: true, send_enabled: false, send_scope: null, recipients: [], send_by: null, send_at: null, version: 3 },
  bundle_outdated: false, server_id: null, server_manual: false, limits: { mem_limit_mb: null, cpus: null }, version: 3, created_at: at, updated_at: at, credentials: [], secret_grants: [], ...over,
})

export const ssh = (over: Partial<Schema['ServerSshOut']> = {}): Schema['ServerSshOut'] => ({
  host: '203.0.113.10', address: '203.0.113.10', port: 22, username: 'deploy', host_key_algorithm: 'ssh-ed25519', host_key_fingerprint: 'SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG',
  host_key_confirmed: false, host_key_confirmed_at: null, client_key_installed: false, client_key_fingerprint: null, prepared_at: null,
  auth_method: 'password', password_set: true, credential_revision: 1, connection_status: 'unknown', last_connection_at: null, legacy_key_cleanup_state: 'not_needed', ...over,
})
/** 确认过指纹、测试连接成功、初始化完成的服务器连接信息 */
export const readySsh = (over: Partial<Schema['ServerSshOut']> = {}) => ssh({ host_key_confirmed: true, host_key_confirmed_at: at, connection_status: 'connected', last_connection_at: at, prepared_at: at, ...over })

export const server = (over: Partial<Schema['AgentServerOut']> = {}): Schema['AgentServerOut'] => ({
  id: 'srv-1', name: '香港节点 A', kind: 'ssh', ssh: ssh(), reported_at: null, stale: true, cores: null, cpu_percent: null, load15: null, mem_total: null, mem_used_percent: null, disk_used_percent: null,
  instances_total: 0, instances_online: 0, alerts: [], initialization_status: 'not_initialized', deployable: false, deploy_block_reason: 'host_key_unconfirmed',
  managed_deployments: 0, successful_deployments: 0, deployment_counts_partial: false, allocated_mem_limit_mb: null, allocated_cpus: null, has_unlimited_limits: false,
  version: 1, created_at: at, updated_at: at, ...over,
})
export const readyServer = (over: Partial<Schema['AgentServerOut']> = {}) => server({ ssh: readySsh(), initialization_status: 'ready', deployable: true, deploy_block_reason: null, ...over })

export const summary = (over: Partial<Schema['AgentServerSummary']> = {}): Schema['AgentServerSummary'] => ({
  server_count: 0, ready_count: 0, successful_deployments: 0, not_initialized_count: 0, needs_credentials_count: 0, deployment_counts_partial: false, generated_at: at, ...over,
})
export const serverList = (items: Schema['AgentServerOut'][], over: Partial<Schema['AgentServerList']> = {}): Schema['AgentServerList'] => ({
  items, unassigned: [], summary: summary({ server_count: items.length, ready_count: items.filter(s => s.initialization_status === 'ready').length, not_initialized_count: items.filter(s => s.kind === 'ssh' && s.initialization_status === 'not_initialized').length }), ...over,
})

export const PREPARE_STEPS = ['connect', 'inspect', 'docker', 'reconnect'] as const
export const job = (kind: Schema['AgentSshJobOut']['kind'], status: Schema['AgentSshJobOut']['status'], over: Partial<Schema['AgentSshJobOut']> = {}): Schema['AgentSshJobOut'] => ({
  id: `job-${kind}`, kind, status, log: '$ sudo -S true\npassword=***', error_code: null, error_message: null, started_at: at, finished_at: status === 'running' ? null : at, updated_at: at,
  steps: PREPARE_STEPS.map((key, i) => ({ key, status: status === 'succeeded' ? 'done' : i < 2 ? 'done' : i === 2 ? 'running' : 'pending', detail: null })), ...over,
})
export const detail = (s: Schema['AgentServerOut'], over: Partial<Schema['AgentServerDetail']> = {}): Schema['AgentServerDetail'] => ({ server: s, current: null, range: '24h', points: [], agents: [], job: null, warning: null, ...over })

export const load = (over: Partial<Schema['AgentLoad']> = {}): Schema['AgentLoad'] => ({ reported_at: at, stale: false, available: true, cpu_cores: 1, cpu_percent: 25, mem_bytes: 4 * GB, mem_limit: null, mem_total: 16 * GB, pids: 30, data_bytes: GB, data_truncated: false, rx_rate: 1024, tx_rate: 512, restarts_1h: 0, ...over })
export const row = (id: string, name: string, over: Partial<Schema['ServerAgentRow']> = {}): Schema['ServerAgentRow'] => ({
  instance_id: id, name, status: 'online', deployed: true, deployment_state: 'deployed', latest_job: null, deployment_project: `myworkspace-openclaw-${id}`, install_dir: `/home/deploy/myworkspace-openclaw/${id}`,
  mem_limit_mb: null, cpus: null, load: load(), alerts: [], tokens_1h: 0, running_task: null, ...over,
})
export const current: Schema['HostCurrent'] = { reported_at: at, stale: false, cpu_model: 'Test CPU', cores: 4, cpu_percent: 60, load: [1, 1, 1], mem_total: 16 * GB, mem_available: 4 * GB, mem_used: 12 * GB, mem_limit: null, swap_total: 0, swap_used: 0, data_disk: { total: 100 * GB, free: 50 * GB, used: 50 * GB, used_percent: 50 }, root_disk: { total: 100 * GB, free: 50 * GB, used: 50 * GB, used_percent: 50 }, rx_rate: 2048, tx_rate: 1024, uptime_seconds: 86400 }
export const deploymentState = (over: Partial<Schema['AgentDeploymentState']> = {}): Schema['AgentDeploymentState'] => ({ deployment: null, server: null, job: null, capacity_warning: null, ...over })

/** OpenClaw 智能体总览：summary 按全部实例统计（测试里由调用方给出，不从 items 推算） */
export const overviewSummary = (over: Partial<Schema['AgentOverviewSummary']> = {}): Schema['AgentOverviewSummary'] => ({ total: 0, online: 0, offline: 0, pending: 0, disabled: 0, deploying: 0, attention: 0, ...over })
export const overviewRow = (inst: Schema['AgentInstanceOut'], over: Partial<Schema['AgentOverviewRow']> = {}): Schema['AgentOverviewRow'] => ({ instance: inst, server: null, deployment: null, latest_job: null, deployment_project: null, ...over })
export function overviewOf(items: Schema['AgentInstanceOut'][], over: Partial<Schema['AgentOverviewOut']> = {}): Schema['AgentOverviewOut'] {
  const count = (status: Schema['AgentInstanceOut']['status']) => items.filter(i => i.status === status).length
  return {
    items: items.map(i => overviewRow(i)), next_cursor: null, generated_at: at,
    summary: overviewSummary({ total: items.length, online: count('online'), offline: count('offline'), pending: count('pending'), disabled: count('disabled'), attention: count('offline') }),
    ...over,
  }
}

export const path = (r: Request) => new URL(r.url).pathname
export const fail = (status: number, code: string, details: Record<string, unknown> = {}, headers: Record<string, string> = {}) => Response.json({ error: { code, message: 'x', request_id: 'req', details } }, { status, headers })
