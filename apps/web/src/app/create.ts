import type { Schema } from '../services/api'

// 顶栏“创建”菜单的条目（规范 §8.1）。每一项跳到对应页面并打开那里已有的新建表单（见 useCreateIntent）；
// 按角色只列出能用的项，和侧栏导航、路由守卫的可见范围一致。真正的权限仍由服务端校验。
export interface CreateItem { label: string; path: string; hint: string }
export interface CreateGroup { label: string; items: CreateItem[] }

export function createGroups(role: Schema['WorkspaceOut']['role'] | undefined): CreateGroup[] {
  if (!role || role === 'viewer') return []
  const groups: CreateGroup[] = [{ label: '内容', items: [
    { label: '新建任务', path: '/tasks', hint: '任务' },
    { label: '新建项目', path: '/projects', hint: '项目' },
    { label: '新建日程', path: '/calendar', hint: '日历' },
    { label: '新建笔记', path: '/knowledge/notes/new', hint: '知识库' },
    { label: '收藏文档', path: '/knowledge/docs', hint: 'AFFiNE 链接' },
  ] }]
  if (role === 'owner' || role === 'admin') {
    groups.push({ label: '管理', items: [
      { label: '新建群聊', path: '/im', hint: '与 OpenClaw 实例' },
      { label: '添加成员', path: '/settings', hint: '工作空间' },
      // 智能体服务器与 OpenClaw 智能体分成两页后（T-0181 / T-0182）：部署智能体、添加服务器；手动登记在智能体页的次要操作里
      ...(role === 'owner' ? [{ label: '部署 OpenClaw 智能体', path: '/agents', hint: '选择服务器' }, { label: '添加智能体服务器', path: '/agent-servers', hint: 'IP、账号与密码' }] : []),
    ] })
  }
  return groups
}
