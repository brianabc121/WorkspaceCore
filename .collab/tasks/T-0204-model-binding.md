---
id: T-0204
title: 中转站管理（MC-4）：智能体主模型绑定与实例接口
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/domain/agent_model_bindings.py,services/api/app/api/v1/agent_model_bindings.py,services/api/app/api/agent_api.py,services/api/tests
depends_on: T-0201, T-0202
created: 2026-11-09
updated: 2026-11-10
---

# T-0204 中转站管理（MC-4）：智能体主模型绑定与实例接口

## 目标
智能体主模型绑定：目标与已应用分开保存；实例接口取自己的非敏感配置、按连接取 Key、回报应用结果；新部署选择连接即授权。（MC-4）方案见 `docs/v2/model-connections/`（设计、接口约定、三个实施计划、迁移手册、验收矩阵、效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。

## 验收标准
- [x] 绑定要求同一工作空间、已授权、启用且完整、模型在明确集合内、协议已开放
- [x] 保存只是目标（pending），实例确认后才 applied；迟到的旧确认不覆盖新目标
- [x] 切换在途时旧的已应用连接仍可取 Key，确认后退休；停用 / 撤权 / 解除绑定立即拒绝
- [x] 实例只能读写自己的绑定；新部署在同一事务里建实例、授权与绑定，幂等重放不重复授权

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 23:42 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 00:09 UTC · claude · 开始任务
- 2026-11-10 01:38 UTC · claude · status in_progress → done：主模型绑定：目标与已应用分开、迟到回报 409、切换在途旧连接可取、新部署同一事务；test_agent_model_bindings.py 9 个测试通过
