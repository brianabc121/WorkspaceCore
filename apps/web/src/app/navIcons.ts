import type { Component } from 'vue'
import { Activity, BookOpen, Bot, CalendarDays, Code, FolderKanban, KeyRound, LayoutDashboard, Lock, MessageSquare, Plug, ScrollText, Settings, ShieldCheck, SquareCheckBig, Workflow } from '@lucide/vue'

// 侧栏每一项的图标（@lucide/vue，ISC 许可，T-0083）。没有登记的路径用总览的图标。
const icons: Record<string, Component> = {
  '/': LayoutDashboard,
  '/tasks': SquareCheckBig,
  '/projects': FolderKanban,
  '/knowledge': BookOpen,
  '/calendar': CalendarDays,
  '/im': MessageSquare,
  '/developer': Code,
  '/operations': Activity,
  '/automations': Workflow,
  '/agents': Bot,
  '/ai': ShieldCheck,
  '/settings': Settings,
  '/settings/integrations': Plug,
  '/settings/security': Lock,
  '/agents/secrets': KeyRound,
  '/settings/audit': ScrollText,
}

export function navIcon(path: string): Component {
  return icons[path] ?? LayoutDashboard
}

// 角色的中文名，显示在侧栏底部的账户区域
export const roleNames: Record<string, string> = { owner: '所有者', admin: '管理员', member: '成员', viewer: '只读' }
