import type { Schema } from '../services/api'

// 顶栏“创建”菜单的条目（规范 §8.1）。每一项跳到对应页面并打开那里已有的新建表单（见 useCreateIntent）；
// 按角色只列出能用的项，和侧栏导航、路由守卫的可见范围一致。真正的权限仍由服务端校验。
export interface CreateItem { label: string; path: string; hint: string }
export interface CreateGroup { label: string; items: CreateItem[] }

export function createGroups(role: Schema['WorkspaceOut']['role'] | undefined): CreateGroup[] {
  if (!role || role === 'viewer') return []
  const groups: CreateGroup[] = [{ label: '内容', items: [
    { label: '新建任务', path: '/tasks', hint: 'Vikunja' },
    { label: '新建日程', path: '/calendar', hint: '日历' },
    { label: '收藏文档', path: '/knowledge', hint: 'AFFiNE 链接' },
  ] }]
  if (role === 'owner' || role === 'admin') {
    groups.push({ label: '管理', items: [
      { label: '新建群聊', path: '/im', hint: '与 OpenClaw 实例' },
      { label: '添加成员', path: '/settings', hint: '工作空间' },
      ...(role === 'owner' ? [{ label: '登记 OpenClaw 实例', path: '/agents', hint: '接入包' }] : []),
    ] })
  }
  return groups
}
