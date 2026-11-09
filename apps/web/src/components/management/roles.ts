import type { Schema } from '../../services/api'

// 工作空间角色的中文名（T-0189）：页面显示“所有者 owner”这样中英并列，接口值不变
export type WorkspaceRole = Schema['MemberOut']['role']
export const ROLE_NAMES: Record<WorkspaceRole, string> = { owner: '所有者', admin: '管理员', member: '成员', viewer: '访客' }
export const roleName = (role: string | null | undefined) => (role && role in ROLE_NAMES ? ROLE_NAMES[role as WorkspaceRole] : role ?? '')
/** 头像用的首字母：取显示名的第一个字（没有就取邮箱） */
export const initialOf = (name: string | null | undefined, fallback = '?') => (name?.trim() || fallback).slice(0, 1).toUpperCase()
