export const navigation = [
  { path: '/', title: '总览', section: '工作空间', description: '个人工作台的统一入口。' },
  { path: '/tasks', title: '任务', section: '工作空间', description: '新建、编辑任务，按列表或看板查看。' },
  { path: '/projects', title: '项目', section: '工作空间', description: '新建、改名、归档或删除项目。' },
  { path: '/knowledge', title: '知识库', section: '工作空间', description: '笔记与知识图谱：公共、项目、智能体三类知识库；AFFiNE 文档收藏在“外部文档”。' },
  { path: '/calendar', title: '日历', section: '工作空间', description: '工作空间共享日程与周视图。' },
  { path: '/news', title: '早报', section: '工作空间', description: '每天的新闻早报、热榜、RSS、搜索与收藏（成员与访客都能看）。' },
  { path: '/im', title: '消息', section: '工作空间', description: '与 OpenClaw 实例单聊、群聊（owner 与 admin）。' },
  { path: '/mail', title: '邮件', section: '工作空间', description: '查收、阅读、回复 Gmail 邮件（owner 与 admin）。' },
  { path: '/developer', title: '开发者', section: '连接与工具', description: 'GitHub 仓库、Issue、PR 与 Actions 只读视图。' },
  { path: '/operations', title: '运维', section: '连接与工具', description: '服务状态、预设指标与 Grafana 外链。' },
  { path: '/agents', title: 'OpenClaw 实例', section: '连接与工具', description: '登记实例、接入包、凭证与密钥授权（owner）。' },
  { path: '/runner', title: '运行服务器', section: '连接与工具', description: '智能体执行命令的容器服务器：环境、模板、运行记录与回收策略（owner 与 admin）。' },
  { path: '/ai', title: 'AI 审批', section: '连接与工具', description: '审批 OpenClaw 实例的写操作，查看每一次工具调用。' },
  { path: '/settings', title: '设置', section: '管理', description: '管理个人资料、工作空间与成员（成员由 owner / admin 管理）。' },
  { path: '/settings/integrations', title: '集成设置', section: '管理', description: '管理员配置 GitHub、测试权限与同步。' },
  { path: '/settings/security', title: '安全设置', section: '管理', description: '管理密码与登录会话。' },
  { path: '/settings/mail', title: '邮件设置', section: '管理', description: '连接 Gmail 邮箱、设置 OAuth 客户端与智能体访问（owner 与 admin）。' },
  { path: '/settings/news', title: '新闻早报', section: '管理', description: '早报的推送时间、来源、兴趣主题、大模型与推送渠道（owner 修改，admin 只读）。' },
  { path: '/agents/secrets', title: '密钥库', section: '管理', description: 'OpenClaw 实例使用的密钥与取用记录（owner）。' },
] as const

// Pages only owner / admin can open; hidden from members and guarded by the router.
export const adminOnly: ReadonlySet<string> = new Set(['/settings/integrations', '/im', '/mail', '/settings/mail', '/settings/news', '/runner'])
// Pages only the owner can open (the agents API is owner-only, OC-1 contract §3).
export const ownerOnly: ReadonlySet<string> = new Set(['/agents', '/agents/secrets'])
