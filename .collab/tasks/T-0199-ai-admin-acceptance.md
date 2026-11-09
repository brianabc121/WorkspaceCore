---
id: T-0199
title: AI 审批与管理区（验收）：八页浏览器验收、截图与文档
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: tests/e2e,.github/workflows/ci-infra.yml,docs
depends_on: T-0189, T-0190, T-0191, T-0192, T-0193, T-0194, T-0195, T-0196, T-0197, T-0198
created: 2026-11-09
updated: 2026-11-09
---

# T-0199 AI 审批与管理区（验收）：八页浏览器验收、截图与文档

## 目标
八页的浏览器验收、截图与文档。方案见 `docs/v2/ai-admin-redesign/`（八页规范、接口约定、三个子计划、8 张效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。

## 验收标准
- [ ] 浏览器验收覆盖八页（桌面 / 手机、亮 / 暗、键盘）与关键异常：AI 过期 / 并发决定、GitHub 缺权 / 限流、密码 / 会话错误、多邮箱重连 / 分配冲突、新闻只读 / 停用、密钥轮换冲突、审计跨页筛选 / 导出超限
- [x] api-contract 与相关文档更新；核验记录写进 docs/verification
- [ ] 前后端完整质量门槛与 collab check 通过，真实输出写进 PR

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 12:51 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 13:22 UTC · claude · 开始任务
- 2026-11-09 14:43 UTC · claude · 全量后端 1124 passed / 15 skipped；前端 74 文件 725 测试、lint / typecheck / build / check:api 通过；核验记录写好（d2e460c）。下一步：#181 合并后 rebase、开 PR、等 ci-infra 浏览器验收
