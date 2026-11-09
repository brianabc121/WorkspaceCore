---
id: T-0201
title: 中转站管理（MC-1）：连接、内部凭据与授权
owner: claude
status: in_progress
priority: P1
branch: b6-model-connections
paths: services/api/app/models/model_connection.py,services/api/app/schemas/model_connections.py,services/api/app/domain/model_connections.py,services/api/app/api/v1/model_connections.py,services/api/migrations,services/api/tests,services/api/openapi.json
depends_on: T-0200
created: 2026-11-09
updated: 2026-11-09
---

# T-0201 中转站管理（MC-1）：连接、内部凭据与授权

## 目标
新增模型连接资源：元数据 + 内部加密凭据（复用 agent_secrets）+ 授权；owner 管理接口、汇总、搜索分页、Key 更换、停用影响、删除冲突。（MC-1）方案见 `docs/v2/model-connections/`（设计、接口约定、三个实施计划、迁移手册、验收矩阵、效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。

## 验收标准
- [ ] 迁移 0056 只新增表，升级 / 降级 / alembic check 通过
- [ ] 增删改查、汇总、搜索、分页、授权接口，只给 owner，跨工作空间 404
- [ ] 任何响应不含 Key 明文、密文或内部凭据 ID
- [ ] 在用连接改 URL / 协议 409（旧导入项首次补齐例外）；停用先确认影响；有授权 / 绑定 / 任务时删除 409
- [ ] 撤销授权同时撤掉这条凭据对该实例的旧授权；版本冲突与创建幂等有测试

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 23:42 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 23:43 UTC · claude · 开始任务
