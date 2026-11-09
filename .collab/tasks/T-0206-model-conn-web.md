---
id: T-0206
title: 中转站管理（MC-6）：中转站管理页与迁移兼容凭据
owner: claude
status: todo
priority: P1
branch:
paths: apps/web/src/features/model-connections,apps/web/src/pages,apps/web/src/app,apps/web/src/features/agents,apps/web/tests
depends_on: T-0201, T-0202, T-0203
created: 2026-11-09
updated: 2026-11-09
---

# T-0206 中转站管理（MC-6）：中转站管理页与迁移兼容凭据

## 目标
中转站管理页（效果图）：汇总、页签、搜索筛选、连接表、详情（配置 / 模型 / 授权 / 活动）、新建 / 编辑、更换 Key、迁移与兼容凭据；旧 /agents/secrets 跳转。（MC-6）方案见 `docs/v2/model-connections/`（设计、接口约定、三个实施计划、迁移手册、验收矩阵、效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。

## 验收标准
- [ ] 汇总与列表全部来自服务端，total 不由分页推算
- [ ] 官方 / 第三方 / 多模型 / 全部可用模型 / 手工填写；Key 不回显、空白不提交
- [ ] 429 / 401 / 不支持 / 过时结果分别展示；迁移草稿与非模型凭据可操作
- [ ] 切换工作空间与迟到响应不串数据；1440 / 390、亮暗无横向滚动

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 23:42 UTC · claude · 创建任务（owner: claude）
