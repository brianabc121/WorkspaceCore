export const navigation = [
  { path: '/', title: '总览', section: '工作空间', description: '个人工作台的统一入口。' },
  { path: '/tasks', title: '任务', section: '工作空间', description: '任务中心将在 Sprint 3 接入 Vikunja。' },
  { path: '/projects', title: '项目', section: '工作空间', description: '项目列表将在 Sprint 3 接入 Vikunja。' },
  { path: '/knowledge', title: '知识库', section: '工作空间', description: '文档深链接和收藏将在 Sprint 4 开放。' },
  { path: '/calendar', title: '日历', section: '工作空间', description: '本地日程将在 Sprint 4 开放。' },
  { path: '/developer', title: '开发者', section: '连接与工具', description: 'GitHub 只读视图将在 Sprint 4 开放。' },
  { path: '/operations', title: '运维', section: '连接与工具', description: '服务状态与监控将在 Sprint 5 开放。' },
  { path: '/automations', title: '自动化', section: '连接与工具', description: '受控自动化入口将在 Sprint 5 开放。' },
  { path: '/ai', title: 'AI 助手', section: '连接与工具', description: 'AI 工具与审批将在 Sprint 6 开放。' },
  { path: '/settings', title: '设置', section: '管理', description: '工作空间设置将在 Sprint 1 开放。' },
  { path: '/settings/integrations', title: '集成设置', section: '管理', description: '集成配置将在后续 Sprint 开放。' },
  { path: '/settings/security', title: '安全设置', section: '管理', description: '账号与会话管理将在 Sprint 1 开放。' },
] as const
