---
id: T-0101
title: 升级方案与核验：时间轴看板、知识图谱按类型着色与取消人已确认、知识库图片（R2 / OSS / 本地）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs,.collab
depends_on:
created: 2026-11-04
updated: 2026-11-04
---

# T-0101 升级方案与核验：时间轴看板、知识图谱按类型着色与取消人已确认、知识库图片（R2 / OSS / 本地）

## 目标
写升级方案（时间轴看板、知识图谱按类型着色、取消“人已确认”、知识库图片存储），核验 OpenClaw 的 token 用量接口与 R2 / OSS 的 S3 兼容接口，建实施任务。

## 验收标准
- [ ] `docs/v2/TIMELINE_PLAN.md`：需求、推荐做法、数据、接口、风险、实施任务
- [ ] 核验 ADR：OpenClaw 2026.9.36 的 `sessions.usage` 在运行实例上实测；R2 / OSS 能确认的与未确认的分开写
- [ ] 建 T-0102 ~ T-0106，填好目标与验收标准
- [ ] `docs/v2/README.md` 与 `KNOWLEDGE_PLAN.md` 指向新方案

## 背景 / 约束 / 参考
- 方案：`docs/v2/TIMELINE_PLAN.md`（人类 2026-11-04 批准：“之后按照推荐的来，给我全面落实”）
- 核验：`docs/decisions/2026-11-04-timeline-kb-upgrade-verification.md`
- 效果图：https://claude.ai/artifact/CHuDA9DsafbAudwxB6A61y

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 01:56 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 01:56 UTC · claude · 开始任务
- 2026-11-04 01:56 UTC · claude · 完成：方案 docs/v2/TIMELINE_PLAN.md（人类 2026-11-04 已批准“按照推荐的来，全面落实”）；核验 ADR：OpenClaw 2026.9.36 的 sessions.usage 在本机真实网关 + 假模型上实测（字段、range 常量、specific 模式、冷缓存 null）；R2 / OSS 只能读到官方文档搜索摘要（本环境网络拦截了 developers.cloudflare.com、help.aliyun.com），按通用 S3 + 部署自检实现，真实账号验收列为 TODO；建 T-0102 ~ T-0106。
- 2026-11-04 01:56 UTC · claude · status in_progress → done：方案与核验完成，随 PR 合并生效
