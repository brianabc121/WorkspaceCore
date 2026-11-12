import type { MinRole, NavEntry, NavSection } from '../modules/types'
import { moduleNav } from '../modules/registry'

// 工作台核心功能的侧栏项。插拔式模块的入口写在各自的 src/modules/<key>/index.ts 里，按 after 合并进来（T-0159）。
// minRole：admin = 只有 owner / admin 能看到和打开，owner = 只有 owner（agents 接口只给 owner，OC-1 契约第 3 节）；路由守卫同样检查。
const core: NavEntry[] = [
  { path: '/', title: '总览', section: '工作空间', description: '个人工作台的统一入口。' },
  { path: '/tasks', title: '任务', section: '工作空间', description: '新建、编辑任务，按列表或看板查看。' },
  { path: '/projects', title: '项目', section: '工作空间', description: '新建、改名、归档或删除项目。' },
  { path: '/knowledge', title: '知识库', section: '工作空间', description: '笔记与知识图谱：公共、项目、智能体三类知识库；AFFiNE 文档收藏在“外部文档”。' },
  { path: '/calendar', title: '日历', section: '工作空间', description: '工作空间共享日程与周视图。' },
  { path: '/im', title: '消息', section: '工作空间', description: '与 OpenClaw 实例单聊、群聊（owner 与 admin）。', minRole: 'admin' },
  { path: '/mail', title: '邮件', section: '工作空间', description: '查收、阅读、回复 Gmail 邮件（owner 与 admin）。', minRole: 'admin' },
  { path: '/developer', title: '开发者', section: '连接与工具', description: 'GitHub 仓库、Issue、PR 与 Actions 只读视图。' },
  { path: '/operations', title: '运维', section: '连接与工具', description: '服务状态、预设指标与 Grafana 外链。' },
  // 智能体服务器与 OpenClaw 智能体是两个独立页面（T-0181 / T-0182）：服务器用 IP、端口、账号和密码添加并初始化，智能体选服务器自动部署
  { path: '/agent-servers', title: '智能体服务器', section: '连接与工具', description: '用 IP、端口、账号和密码添加服务器，初始化运行环境，查看上面部署的智能体（owner）。', minRole: 'owner' },
  { path: '/agents', title: 'OpenClaw 智能体', section: '连接与工具', description: '选择已初始化的服务器自动部署 OpenClaw，或手动登记接入；配置、权限、凭证与运维（owner）。', minRole: 'owner' },
  { path: '/runner', title: '容器服务器', section: '连接与工具', description: 'OpenClaw 申请的容器：归属、CPU / 内存 / 数据卷负载、执行记录、模板与回收（owner 与 admin）。', minRole: 'admin' },
  { path: '/ai', title: 'AI 审批', section: '连接与工具', description: '审批 OpenClaw 实例的写操作，查看每一次工具调用。' },
  { path: '/settings', title: '设置', section: '管理', description: '管理个人资料、工作空间与成员（成员由 owner / admin 管理）。' },
  { path: '/settings/modules', title: '应用中心', section: '管理', description: '统一管理功能模块、页面入口与扩展能力：启停、部署与连通状态、同步结果、操作记录（owner 修改，admin 只读）。', minRole: 'admin' },
  { path: '/settings/navigation', title: '侧栏与页面', section: '管理', description: '自定义侧栏分组、把页面挪到别的分组、添加链接、笔记、仪表盘和嵌入页（owner 修改，admin 只读）。', minRole: 'admin' },
  { path: '/settings/integrations', title: '集成设置', section: '管理', description: '管理员配置 GitHub、测试权限与同步。', minRole: 'admin' },
  { path: '/settings/security', title: '安全设置', section: '管理', description: '管理密码与登录会话。' },
  { path: '/settings/mail', title: '邮件设置', section: '管理', description: '连接 Gmail 邮箱、设置 OAuth 客户端与智能体访问（owner 与 admin）。', minRole: 'admin' },
  // 图片存储（T-0223）：知识库图片与常驻环境快照存在本机、Cloudflare R2 还是阿里云 OSS，按工作空间设置
  { path: '/settings/storage', title: '图片存储', section: '管理', description: '知识库图片存在本机、Cloudflare R2 还是阿里云 OSS，副本与连接信息（owner 修改，admin 只读）。', minRole: 'admin' },
  // 中转站管理（T-0206）：官方 API 与第三方中转的连接；非模型的兼容凭据（原来的“密钥库”）在它的“兼容凭据”页签
  { path: '/settings/model-connections', title: '中转站管理', section: '管理', description: '官方 API 与第三方中转的连接（URL、Key、模型）、智能体授权、OpenClaw 连接分配与兼容凭据（owner）。', minRole: 'owner' },
]

/** 侧栏分组的顺序：“应用”是新模块默认的分组 */
const SECTIONS: readonly NavSection[] = ['工作空间', '应用', '连接与工具', '管理']

/** 侧栏项；module 有值的属于插拔式模块，模块停用时隐藏 */
export interface NavItem extends NavEntry { module?: string }

function merge(): NavItem[] {
  const items: NavItem[] = [...core]
  const placed = new Set<NavItem>()
  for (const { after, ...entry } of moduleNav) {
    // 排在 after 那一项后面（之前已经排在它后面的模块项之后）；找不到就排在同一分组最后，分组还没有就排在最后
    let index = after ? items.findIndex((item) => item.path === after) : -1
    if (index >= 0) { while (index + 1 < items.length && placed.has(items[index + 1]!)) index++ }
    else index = items.map((item) => item.section).lastIndexOf(entry.section)
    const item: NavItem = entry
    items.splice(index >= 0 ? index + 1 : items.length, 0, item)
    placed.add(item)
  }
  return items.map((item, order) => ({ item, order })).sort((a, b) => SECTIONS.indexOf(a.item.section) - SECTIONS.indexOf(b.item.section) || a.order - b.order).map(({ item }) => item)
}

export const navigation: readonly NavItem[] = merge()

/** 当前角色能不能看到这一项（与路由守卫的 meta.admin / meta.owner 一致） */
export function canSee(entry: { minRole?: MinRole }, access: { canManage: boolean; isOwner: boolean }) {
  return !entry.minRole || (entry.minRole === 'owner' ? access.isOwner : access.canManage)
}
