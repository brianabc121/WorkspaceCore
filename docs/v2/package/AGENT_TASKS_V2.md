# Agent Control Plane V2 可执行任务清单

> 必须先完成旧版 Sprint 0–5 的必要基础模块，然后优先 V2-S0 进行能力核验。每次只实现一个任务，附真实命令和测试输出。

## V2-S0 — 能力核验
- [ ] 固定 OpenClaw、Codex App Server 版本与 OS
- [ ] 使用真实环境完成 Gateway WS handshake / session API smoke
- [ ] 完成 Codex initialize/initialized、thread/list、turn/start、approval 测试
- [ ] 生成 Codex App Server JSON schema 并保存兼容性说明
- [ ] 制作 Codex Cloud 功能矩阵：已验证/未验证/不支持并提交 ADR

## V2-S1 — 控制面基础
- [ ] 新增 Agent Registry / Policies / Jobs / Sessions / Events / Approvals 迁移
- [ ] Credential secret_ref、RBAC、workspace scope
- [ ] Provider adapter Protocol + fake provider contract tests
- [ ] Agent health、availability、capabilities API

## V2-S2 — Codex Adapter
- [ ] Codex Bridge WS/stdio 连接、断线重连
- [ ] Thread list/read/start/resume、turn/start/interrupt
- [ ] JSON-RPC 通知事件持久化并规范化
- [ ] 隔离 worktree、Git 状态和 diff 采集
- [ ] 审批请求转发与决策回送测试

## V2-S3 — OpenClaw Adapter
- [ ] Gateway 认证 WS 与 session list/history/send
- [ ] 事件订阅、幂等去重和状态恢复
- [ ] Exec Approval 桥接，权限验证
- [ ] Cron / Hooks 查询与可选状态映射

## V2-S4 — Job Dispatcher
- [ ] Task resource -> Agent Job 映射、幂等创建
- [ ] Job 队列、Worker 运行、并发与限流
- [ ] 租约/心跳、孤儿任务对账、取消语义
- [ ] GitHub Repo allowlist、执行目录隔离

## V2-S5 — Web/PWA
- [ ] Agent 首页、实例列表、运行列表
- [ ] Session Viewer：历史、增量事件、工具调用、差异
- [ ] 审批中心：权限、过期与重复点击保护
- [ ] 移动适配、SSE 断线重连、通知

## V2-S6 — 安全验收
- [ ] 三层审批和高风险操作二次确认
- [ ] CSRF/SSRF/IDOR/凭证脱敏/跨工作空间数据测试
- [ ] OpenClaw allowlist+ask 和 Codex sandbox 默认配置审计
- [ ] 故障注入：Worker/Bridge/Gateway 重启与断网

## V2-S7 — 发布
- [ ] GitHub 工件、PR/CI 关联和可追溯审核
- [ ] 运行统计与可选 token/cost 记录
- [ ] 备份恢复演练、日志保留、容量/延迟验收
- [ ] 运维手册、回滚、最小权限部署检查

