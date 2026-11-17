import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import type { RunnerHost } from './api'
import { binary } from './containerMetrics'

// 容器服务器的读数与告警（列表与详情共用；原主机条 ContainerHostStrip 的判断，T-0186，样式按智能体服务器页重排 T-0277）。
// “最近联系”是 runner-agent 长轮询的心跳（last_seen_at），“完整报告”是对账上报的时间（report.at）——读数跟着报告走，
// 不把心跳说成读数更新。读不到就写“—”或说明原因，不补 0。
export type Candidate = Schema['RunnerHostCandidateOut']
export type Tone = 'green' | 'red' | 'amber' | 'muted' | 'blue'

export const HOST_TONE: Record<RunnerHost['status'], Tone> = { online: 'green', offline: 'red', pending: 'amber', disabled: 'muted' }
export const INGRESS: Record<RunnerHost['ingress'], string> = { intranet: '内网入口', public: '外网入口' }
// 自动分配的排除原因（GET /runner/hosts/candidates）
export const REASON: Record<string, string> = {
  offline: '节点离线', disabled: '节点已停用', metrics_stale: '资源采样已过期', metrics_unknown: '资源状态未知', runtime_unavailable: '运行时未就绪',
  network_unavailable: '网络未就绪', memory_insufficient: '内存不足', cpu_insufficient: 'CPU 不足', disk_insufficient: '磁盘不足',
}

const num = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : null)
const ratio = (used: number | null, total: number) => (used !== null && total ? Math.min(1, Math.max(0, used / total)) : 0)

export function hostMetrics(host: RunnerHost) {
  const report = (host.report ?? {}) as Record<string, unknown>
  const memTotal = num(report.mem_bytes) || host.mem_bytes || 0
  const memFree = num(report.mem_free_bytes)
  const memUsed = memTotal && memFree !== null ? memTotal - memFree : null
  const diskTotal = num(report.disk_bytes) || host.disk_bytes || 0
  const diskFree = num(report.disk_free_bytes) ?? host.disk_free_bytes ?? null
  const diskUsed = diskTotal && diskFree !== null ? diskTotal - diskFree : null
  return {
    memTotal, memUsed, memFree: memUsed !== null ? memFree : null, diskTotal, diskFree, diskUsed,
    memRatio: ratio(memUsed, memTotal), diskRatio: ratio(diskUsed, diskTotal),
    load: num(report.load),
    /** 工作台建的、此刻在 Docker 里的容器数（最近一次完整报告）；没报告过为 null */
    containers: num(report.containers),
    imagesBytes: num(report.images_bytes), containersBytes: num(report.containers_bytes), volumesBytes: num(report.volumes_bytes),
    reportAt: typeof report.at === 'string' ? report.at : null,
    // 旧版 runner-agent 不报 mcp_manager_containers（仍把管理器数进外来容器）：按 0
    foreign: num(report.unlabeled_containers) ?? 0,
    managers: num(report.mcp_manager_containers) ?? 0,
    gvisor: host.runtimes.includes('runsc'),
  }
}

/** 列表里“系统 / 资源”一格：“16 核 · 32 GiB · 磁盘 500 GiB”；还没上报写“等待上报” */
export function hostResources(host: RunnerHost): string {
  const m = hostMetrics(host)
  const parts = [host.cpus ? `${host.cpus} 核` : '', m.memTotal ? binary(m.memTotal) : '', m.diskTotal ? `磁盘 ${binary(m.diskTotal)}` : ''].filter(Boolean)
  return parts.join(' · ') || '等待上报'
}

/** 正在提醒的告警：离线 / 停用、磁盘与内存超过水位、有不是工作台建的容器、没装 gVisor（原主机条的判断） */
export function hostAlerts(host: RunnerHost): string[] {
  const m = hostMetrics(host), policy = host.policy ?? {}, out: string[] = []
  if (host.status === 'pending') return out
  if (host.status === 'offline') out.push(`容器服务器离线：最近一次联系 ${host.last_seen_at ? formatTime(host.last_seen_at) : '从未'}。环境不会被删除，回到在线后继续；离线期间的读数都按“已过期”显示。`)
  if (host.status === 'disabled') out.push('容器服务器已停用：智能体不能新建或使用环境，已有读数按“已过期”显示。')
  if (m.diskRatio > (policy.disk_emergency ?? 0.95)) out.push(`主机磁盘告急（已用 ${Math.round(m.diskRatio * 100)}%）：暂停新建环境，一次性运行已停止。`)
  else if (m.diskRatio > (policy.disk_high ?? 0.8)) out.push(`主机磁盘超过高水位（已用 ${Math.round(m.diskRatio * 100)}%）：正在按保留分归档停止的环境、清理没人用的镜像。`)
  if (m.memRatio > (policy.mem_high ?? 0.85)) out.push('主机内存超过高水位：正在按保留分停止空闲的环境。')
  if (m.foreign > 0) out.push(`服务器上有 ${m.foreign} 个不是工作台建的容器：容器服务器最好专用，工作台不会碰它们。${m.managers > 0 ? `另有 ${m.managers} 个 MCP 管理器（不受这里管理）。` : ''}`)
  if (!m.gvisor && host.runtimes.length) out.push('没装 gVisor（runsc）：智能体只能选 runc。重新部署容器服务器会装上。')
  return out
}
