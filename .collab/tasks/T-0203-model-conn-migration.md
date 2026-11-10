---
id: T-0203
title: 中转站管理（MC-3）：旧密钥幂等导入与兼容凭据
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/commands,services/api/app/domain/agents.py,services/api/app/domain/runner_hosts.py,services/api/tests
depends_on: T-0201
created: 2026-11-09
updated: 2026-11-10
---

# T-0203 中转站管理（MC-3）：旧密钥幂等导入与兼容凭据

## 目标
把旧密钥库按工作空间幂等导入为连接映射（不复制密文、不换 ID），保留授权与取用记录；旧取密钥接口与 Runner 注入统一按连接的启停与授权校验。（MC-3）方案见 `docs/v2/model-connections/`（设计、接口约定、三个实施计划、迁移手册、验收矩阵、效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。

## 验收标准
- [x] 导入命令 --dry-run / --apply / --report，按工作空间显式执行，JSON 输出不含 Key
- [x] model_api_key 成待完善、api_token / generic 成待归类、不可解密成需复核；重复 / 并发执行一对一
- [x] 停用或撤权后旧取密钥接口与 Runner 都拒绝；归类为非模型不恢复访问
- [x] 旧密钥接口对被连接托管的凭据写操作走同一套规则（不能绕过版本与停用）

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 23:42 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 00:29 UTC · claude · 开始任务
- 2026-11-10 01:38 UTC · claude · status in_progress → done：旧密钥库原地纳管：命令与页面（预览导入 / 导入 / 对账）；main 0055 → 0056 升级演练 4 项一对一、重复导入 0 新建、对账一致（核验记录 3.2）
