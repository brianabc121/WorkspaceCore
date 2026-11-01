export const navigation = [
  { path: '/', title: '总览', section: '工作空间', description: '个人工作台的统一入口。' },
  { path: '/tasks', title: '任务', section: '工作空间', description: '查看来自 Vikunja 的任务列表与看板。' },
  { path: '/projects', title: '项目', section: '工作空间', description: '查看已同步的 Vikunja 项目。' },
  { path: '/knowledge', title: '知识库', section: '工作空间', description: 'AFFiNE 文档收藏与项目、任务关联。' },
  { path: '/calendar', title: '日历', section: '工作空间', description: '工作空间共享日程与周视图。' },
  { path: '/im', title: '消息', section: '工作空间', description: '与 OpenClaw 实例单聊、群聊（owner 与 admin）。' },
  { path: '/developer', title: '开发者', section: '连接与工具', description: 'GitHub 仓库、Issue、PR 与 Actions 只读视图。' },
  { path: '/operations', title: '运维', section: '连接与工具', description: '服务状态、预设指标与 Grafana 外链。' },
  { path: '/automations', title: '自动化', section: '连接与工具', description: '受控自动化入口将在 Sprint 5 开放。' },
  { path: '/agents', title: 'OpenClaw 实例', section: '连接与工具', description: '登记实例、接入包、凭证与密钥授权（owner）。' },
  { path: '/ai', title: 'AI 审批', section: '连接与工具', description: '审批 OpenClaw 实例的写操作，查看每一次工具调用。' },
  { path: '/settings', title: '设置', section: '管理', description: '管理个人资料与工作空间。' },
  { path: '/settings/integrations', title: '集成设置', section: '管理', description: '管理员配置 Vikunja / GitHub、测试权限与同步。' },
  { path: '/settings/security', title: '安全设置', section: '管理', description: '管理密码与登录会话。' },
  { path: '/agents/secrets', title: '密钥库', section: '管理', description: 'OpenClaw 实例使用的密钥与取用记录（owner）。' },
] as const

// Pages only owner / admin can open; hidden from members and guarded by the router.
export const adminOnly: ReadonlySet<string> = new Set(['/settings/integrations', '/im'])
// Pages only the owner can open (the agents API is owner-only, OC-1 contract §3).
export const ownerOnly: ReadonlySet<string> = new Set(['/agents', '/agents/secrets'])
