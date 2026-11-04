---
id: T-0106
title: 时间轴与知识库图片的验收与文档：真实 OpenClaw 用量上报、浏览器验收、S3 存储验收、威胁模型与部署文档
owner: claude
status: todo
priority: P1
branch:
paths: tests/e2e,.github/workflows,docs,services/api,apps/web,scripts
depends_on: T-0103, T-0105
created: 2026-11-04
updated: 2026-11-04
---

# T-0106 时间轴与知识库图片的验收与文档：真实 OpenClaw 用量上报、浏览器验收、S3 存储验收、威胁模型与部署文档

## 目标
在真实环境上验收时间轴（含真实 OpenClaw 的 token 上报）和知识库图片，补齐文档。

## 验收标准
- [ ] agents CI：真实 OpenClaw 上报用量，交给智能体执行后时间轴显示执行段与 token，完成审批后段结束；SSH 部署写入上报定时任务
- [ ] compose CI：时间轴浏览器验收（全部 / 按项目、开始暂停、拖动计划、手机宽度、viewer 只读）；知识库图片浏览器验收（插入、粘贴、显示、清理）
- [ ] S3 存储在 Garage 上的验收（主存储 + 副本、读主失败读副本）；本地存储的备份恢复验收
- [ ] 威胁模型、架构、部署手册、运维手册、versions；方案状态改为已完成

## 背景 / 约束 / 参考
- 方案：`docs/v2/TIMELINE_PLAN.md`（人类 2026-11-04 批准：“之后按照推荐的来，给我全面落实”）
- 核验：`docs/decisions/2026-11-04-timeline-kb-upgrade-verification.md`
- 效果图：https://claude.ai/artifact/CHuDA9DsafbAudwxB6A61y

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 01:56 UTC · claude · 创建任务（owner: claude）
