---
id: T-0121
title: 唤醒：schedule_wakeup / cancel_wakeup、管理员建唤醒、/agent-api/v1/wakeups、接入包 wakeup.mjs
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0118
created: 2026-11-04
updated: 2026-11-06
---

# T-0121 唤醒：schedule_wakeup / cancel_wakeup、管理员建唤醒、/agent-api/v1/wakeups、接入包 wakeup.mjs

## 目标
让智能体能被工作台叫醒，主动发起会话（方案第 7.2 节）。

## 验收标准
- [x] 迁移：唤醒表；`schedule_wakeup` / `cancel_wakeup`（限额）；管理员在实例页建唤醒（含重复）
- [x] `/agent-api/v1/wakeups`（取到期的、报结果）；接入包 `wakeup.mjs` 与 crontab 行，SSH 部署自动写入
- [x] 后端与前端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_MESSAGING_PLAN.md`（人类 2026-11-04 批准，D1 ~ D19 按推荐）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 19:02 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 07:52 UTC · claude · 开始任务
- 2026-11-06 08:13 UTC · claude · 迁移 0036（agent_wakeups）；app/domain/agent_wakeups.py：schedule_wakeup / cancel_wakeup（直接执行，例外四；5 分钟 ~ 30 天、最多 20 个待执行、目标只能是 im_list_targets 里的、只能取消自己安排的），待执行的列进 im_outbox；管理员 GET/POST /agents/instances/{id}/wakeups（含可选会话、每天 / 工作日 / 每周按时区钟点重复、审计）与取消；/agent-api/v1/wakeups/claim（领取即执行中、每分钟 6 次）与 /{id}/result（重复的排下一次）；领取后 15 分钟没结果记失败、不重跑；结束 30 天后清理。接入包 bin/wakeup.mjs（锁文件、openclaw agent --session-key --message-file --timeout 600）、crontab 第 5 行、CHECKLIST；实例卡片“唤醒”面板。会话键按 openclaw-channel 0.3.3 的 baseSessionKey 写（openim:group:<群>、openim:<IM ID>，小写），真实 OpenClaw 上的效果（V4）由 e2e 场景 21 实测。tests/test_agent_wakeups.py 5 个、test_wakeup_script.py 2 个、agents-wakeups.test.ts 2 个用例通过。另：CI images 任务因新公布的 CVE（api 的 apt 3.0.3、web 的 zlib 1.3.2-r0）失败，与本任务无关但 main 同样会红；两个 Dockerfile 里构建时只升级这几个包（基础镜像 digest 不变，上游出新构建后删掉）。
- 2026-11-06 08:40 UTC · claude · status in_progress → done：CI agents（84f4359）场景 21 通过：wakeup.mjs 领取唤醒、在群的会话里跑完一轮并报回 done，甲用 im_send 在群里发言（V4）。结果记入核验记录 6.3。
