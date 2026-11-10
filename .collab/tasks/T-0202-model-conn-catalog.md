---
id: T-0202
title: 中转站管理（MC-2）：多模型目录与真实连接检测
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/domain/model_catalog.py,services/api/app/integrations/model_providers.py,services/api/app/api/v1/model_catalog.py,services/api/tests
depends_on: T-0201
created: 2026-11-09
updated: 2026-11-10
---

# T-0202 中转站管理（MC-2）：多模型目录与真实连接检测

## 目标
连接的模型目录同步与检测：经安全出站策略访问服务商，分级的检测结果，过时结果不覆盖新配置。（MC-2）方案见 `docs/v2/model-connections/`（设计、接口约定、三个实施计划、迁移手册、验收矩阵、效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。

## 验收标准
- [x] 指定模型 / 服务商全部可用模型；模型列表不支持时只能手工填写，不显示空成功
- [x] 同步不完整（超 1000 条、响应过大、分页中断）保留上一次完整快照
- [x] 401 / 403 / 429（Retry-After 秒数与日期）/ 5xx / 超时 / TLS / 非 JSON 分别归类；metadata 不等于推理通过
- [x] 检测在途时轮换 Key，旧结果不写回；私网 / 元数据地址 / 重定向被拒绝

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 23:42 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 00:19 UTC · claude · 开始任务
- 2026-11-10 01:38 UTC · claude · status in_progress → done：模型目录、同步与检测（OpenAI 兼容 / Anthropic / Gemini 原生）；test_model_connection_checks.py 覆盖错误归类、429、快照保留、出站策略
