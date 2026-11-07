import type { Component } from 'vue'
import { Activity, BookOpen, Bot, CalendarDays, Code, FolderKanban, KeyRound, LayoutDashboard, Lock, Mail, MailCheck, MessageSquare, Newspaper, Plug, Rss, ScrollText, Server, Settings, ShieldCheck, SquareCheckBig } from '@lucide/vue'

// 侧栏每一项的图标（@lucide/vue，ISC 许可，T-0083）。没有登记的路径用总览的图标。
const icons: Record<string, Component> = {
  '/': LayoutDashboard,
  '/tasks': SquareCheckBig,
  '/projects': FolderKanban,
  '/knowledge': BookOpen,
  '/calendar': CalendarDays,
  '/news': Newspaper,
  '/im': MessageSquare,
  '/mail': Mail,
  '/developer': Code,
  '/operations': Activity,
  '/agents': Bot,
  '/runner': Server,
  '/ai': ShieldCheck,
  '/settings': Settings,
  '/settings/integrations': Plug,
  '/settings/security': Lock,
  '/settings/mail': MailCheck,
  '/settings/news': Rss,
  '/agents/secrets': KeyRound,
  '/settings/audit': ScrollText,
}

export function navIcon(path: string): Component {
  return icons[path] ?? LayoutDashboard
}

// 角色的中文名，显示在侧栏底部的账户区域
export const roleNames: Record<string, string> = { owner: '所有者', admin: '管理员', member: '成员', viewer: '只读' }
