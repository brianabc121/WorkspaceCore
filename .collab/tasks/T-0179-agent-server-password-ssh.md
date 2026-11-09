---
id: T-0179
title: 智能体服务器（二）：初始化、部署、卸载与运维统一用保存的密码登录，旧公钥定向退役
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/domain/agent_servers.py,services/api/app/domain/agent_ssh_install.py,services/api/app/domain/agent_ops.py,services/api/app/domain/agent_ssh.py,services/api/app/domain/agent_server_ssh.py,services/api/app/agent_provisioning,services/api/app/schemas/agents.py,services/api/app/api/v1/agents.py,services/api/tests,services/api/openapi.json,apps/web/src/services/schema.d.ts
depends_on: T-0178
created: 2026-11-09
updated: 2026-11-09
---

# T-0179 智能体服务器（二）：初始化、部署、卸载与运维统一用保存的密码登录，旧公钥定向退役

## 目标
OpenClaw 服务器的初始化、部署、卸载与运维全部改用保存的密码登录，不再生成、安装或使用 SSH 客户端密钥；旧服务器补录密码后定向移除工作台旧公钥。
方案见 `docs/v2/agent-servers-redesign/`（设计规范、接口与迁移约定、六阶段计划、两张效果图）；人类在对话中要求“根据效果图和开发文档全面落实”。（计划 Task 2）

## 验收标准
- [ ] OpenClaw 专用连接策略只用密码 + 固定主机指纹，关闭 ssh-agent / 默认私钥 / publickey 回退；sudo 密码经 stdin
- [ ] 初始化独立（检测系统、Docker / Compose、部署目录、平台可达），不装公钥、不建实例；重复执行幂等，不升级已有 Docker、不全局 prune
- [ ] 部署 / 卸载 / 运维（restart / up / status / logs / prune 等）都用保存的密码；旧私钥存在也不读取；运维二次认证仍是工作台账号密码
- [ ] 新增 `POST /agents/servers/{id}/legacy-key-cleanup`：密码登录后只删除本工作台那一条公钥，其他 authorized_keys 不变；失败标 pending / manual_required；旧 `revoke` 返回 410
- [ ] Runner 的 SSH 流程不变（原测试通过）

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 06:30 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 07:23 UTC · claude · 开始任务
