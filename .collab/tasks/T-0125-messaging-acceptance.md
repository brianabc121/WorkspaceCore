---
id: T-0125
title: 发消息、Issues 与 README 的真实 OpenClaw 验收与文档
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e,infra,.github
depends_on: T-0119, T-0120, T-0121, T-0122, T-0124
created: 2026-11-04
updated: 2026-11-06
---

# T-0125 发消息、Issues 与 README 的真实 OpenClaw 验收与文档

## 目标
用真实 OpenClaw 验收发消息、Issues 与 README，并更新文档（方案第 13 节）。

## 验收标准
- [ ] 真实 OpenClaw 场景：发图片 / 文件 / 视频；拒收的返回与系统事件；OpenIM 重启期间排队、恢复后发出且不重复；定时消息；唤醒后主动发言
- [ ] Issue 对接：开接口约定 Issue 指派另一个智能体、被唤醒后评论；连续评论暂停；阻塞 Issue 挡住完成
- [ ] README 自动生成与更新；浏览器验收
- [ ] 文档：api-contract、data-dictionary、threat-model、deploy-guide、versions；核验记录补实测结果

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_MESSAGING_PLAN.md`（人类 2026-11-04 批准，D1 ~ D19 按推荐）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 19:02 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 13:54 UTC · claude · 开始任务
- 2026-11-06 13:54 UTC · claude · 人类 2026-11-06 要求“继续”后开工。已完成（本地，等 PR #161 合并后提交）：文档（api-contract 消息 / 唤醒 / 终止 / README / Issues 五节、data-dictionary 0033~0039、threat-model 8 行、deploy-guide 8.6、versions）；修正执行记录存附件全文的问题（_record_args 只记长度，被密钥拦下的也不记）；agents 场景 24（附件、定时、OpenIM 停掉期间排队）、25（README、Issue 唤醒与评论、连续评论暂停、阻塞 Issue）；README / Issues 浏览器验收 readme_issues_browser.cjs（本机全部通过）。下一步：开 PR 跑 CI，按结果补核验记录的实测。
