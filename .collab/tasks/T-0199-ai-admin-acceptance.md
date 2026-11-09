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
- 2026-11-09 15:10 UTC · claude · CI secrets：gitleaks 报 4ecda88 里审计测试的 3 处假凭据（tok-SECRET-123、hunter2-SECRET，用来验证导出去掉凭据键），按惯例把指纹登记进 .gitleaksignore（越界改动：安全扫描的误报清单）；本地对 HEAD 历史 456 个提交重扫 no leaks found
- 2026-11-09 15:15 UTC · claude · CI news：news_browser 第 157 行 getByLabel(/^全局排除词/) 同时匹配分区与文本框（严格模式失败），改为按文本框找；推送时间 1 加 exact（有第二个推送时间时还会匹配“删除推送时间 1”）。用 vite + 模拟接口按脚本原样跑设置页到“ntfy：成功”通过
- 2026-11-09 16:01 UTC · claude · CI news（795cdb7）：设置页 5 步都通过，后面第 228 行报 panel is not a function——设置页新加的局部变量 panel 遮住了脚本顶部的 panel(page) 辅助函数（tab 也是顶部函数）。改名为 settingsPanel，并检查全脚本没有局部变量遮住顶层名字
