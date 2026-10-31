import type { Schema } from '../src/services/api'
export const opsTime = '2026-10-31T03:00:00Z'
export const services: Schema['ServiceStatusList'] = { generated_at: opsTime, items: [
  { id: 'api', name: 'API', kind: 'core', status: 'ok', checked_at: opsTime, reason: null, latency_ms: 0 },
  { id: 'worker', name: 'Worker', kind: 'core', status: 'degraded', checked_at: opsTime, reason: 'not_synced' },
  { id: 'postgres', name: 'PostgreSQL', kind: 'core', status: 'down', checked_at: opsTime, reason: 'timeout' },
  { id: 'redis', name: 'Redis', kind: 'core', status: 'unknown', checked_at: null },
  { id: 'prometheus', name: 'Prometheus', kind: 'monitoring', status: 'not_configured', checked_at: null },
] }
export const metrics: Schema['MetricsOut'] = { range: '1h', step_seconds: 60, grafana_url: 'https://dash.example.com/d/workspace', sync: { status: 'ok', as_of: opsTime }, views: [{ id: 'latency_p95', title: 'API P95 延迟', unit: 'seconds', series: [{ label: '', points: [
  { at: '2026-10-31T02:56:00Z', value: 0.2 }, { at: '2026-10-31T02:57:00Z', value: 0.3 }, { at: '2026-10-31T02:58:00Z', value: null }, { at: '2026-10-31T02:59:00Z', value: 0 }, { at: opsTime, value: 0.1 },
] }] }] }
