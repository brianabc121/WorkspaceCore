---
id: T-0160
title: 插拔式模块（三）：新闻早报迁成模块（试点）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,tests/e2e
depends_on:
created: 2026-11-08
updated: 2026-11-08
---

# T-0160 插拔式模块（三）：新闻早报迁成模块（试点）

## 目标
方案 P2：拿新闻早报试点，代码搬进 `services/api/app/modules/news/` 与 `apps/web/src/modules/news/`，
删掉手写登记（工具 if 链、路由列表、定时任务列表、脱敏表、导航和路由映射里的早报条目）。

## 验收标准
- [x] 后端早报代码全部在 `app/modules/news/`，经清单接入；公共文件里不再有早报的手写登记
- [x] 前端早报页面、设置页、小组件在 `src/modules/news/`，经模块注册表接入
- [x] 现有早报测试、接口验收、浏览器验收照常通过
- [x] 停用早报后入口、小组件、工具、定时任务都消失；再启用数据还在（有测试）

## 背景 / 约束 / 参考
方案：https://claude.ai/artifact/AqLi7VYg8UaQVmpWxfibmj（落库后见 `docs/v2/MODULES_PLAN.md`）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-08 12:57 UTC · claude · 创建任务（owner: claude）
- 2026-11-08 12:58 UTC · claude · 开始任务
- 2026-11-08 14:10 UTC · claude · status in_progress → done：新闻早报迁到 app/modules/news 与 src/modules/news；停用后接口、工具、任务、接收地址、推送都停止，再启用数据还在（test_modules.py）
