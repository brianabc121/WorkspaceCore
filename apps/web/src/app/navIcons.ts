import type { Component } from 'vue'
import { Activity, Blocks, BookOpen, Bot, CalendarDays, Code, FolderKanban, LayoutDashboard, Lock, Mail, MailCheck, MessageSquare, PanelsTopLeft, Plug, ScrollText, Server, Settings, ShieldCheck, SquareCheckBig, Waypoints } from '@lucide/vue'
import { moduleNav } from '../modules/registry'

// 侧栏每一项的图标（@lucide/vue，ISC 许可，T-0083）。模块的图标写在模块的 nav 里（T-0159）。没有登记的路径用总览的图标。
const icons: Record<string, Component> = {
  '/': LayoutDashboard,
  '/tasks': SquareCheckBig,
  '/projects': FolderKanban,
  '/knowledge': BookOpen,
  '/calendar': CalendarDays,
  '/im': MessageSquare,
  '/mail': Mail,
  '/developer': Code,
  '/operations': Activity,
  '/agent-servers': Server,
  '/agents': Bot,
  '/runner': Server,
  '/ai': ShieldCheck,
  '/settings': Settings,
  '/settings/modules': Blocks,
  '/settings/navigation': PanelsTopLeft,
  '/settings/integrations': Plug,
  '/settings/security': Lock,
  '/settings/mail': MailCheck,
  '/settings/model-connections': Waypoints,
  '/settings/audit': ScrollText,
  ...Object.fromEntries(moduleNav.flatMap((entry) => (entry.icon ? [[entry.path, entry.icon]] : []))),
}

export function navIcon(path: string): Component {
  return icons[path] ?? LayoutDashboard
}

// 角色的中文名，显示在侧栏底部的账户区域
export const roleNames: Record<string, string> = { owner: '所有者', admin: '管理员', member: '成员', viewer: '只读' }
