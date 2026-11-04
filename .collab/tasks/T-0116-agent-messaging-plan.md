---
id: T-0116
title: 智能体主动发消息优化方案（文字 / 图片 / 文件 / 视频、拒收反馈、会话目录、可靠投递与唤醒）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs
depends_on:
created: 2026-11-04
updated: 2026-11-04
---

# T-0116 智能体主动发消息优化方案（文字 / 图片 / 文件 / 视频、拒收反馈、会话目录、可靠投递与唤醒）

## 目标
人类 2026-11-04 提出四点：1. OpenClaw 能发文字 / 图片 / 文件 / 视频；2. 发出的消息被拒收时 OpenClaw 要知道；
3. MCP 工具里能看到自己在哪些群、能私聊哪些用户，从而主动发起会话；4. 智能体能可靠地主动发消息。要求“给我出一份优化方案”。
本任务只写方案和核验记录，不改代码。

## 验收标准
- [x] `docs/v2/AGENT_MESSAGING_PLAN.md`：现状（已核验）、每一点的做法、MCP 工具、附件、拒收反馈、发件箱与重试、唤醒、安全与权限、
      要人类决定的事项（带推荐）、实施前的核验项、实施任务拆分、风险
- [x] `docs/decisions/2026-11-04-agent-messaging-verification.md`：在锁定版本上已核对的事实（OpenIM v3.8.3-patch.18 源码、
      openclaw-channel 0.3.3、OpenClaw 2026.9.36 镜像里的文档）

## 背景 / 约束 / 参考
- 方案状态为“待批准”；人类批准（含第 9 节的选择）后再按第 11 节建实施任务
- 直接执行的发消息工具需要在 `docs/AGENTS.md` 第 8 节加“例外四”，那一节由人类负责，写法见方案第 8 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 13:34 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 13:34 UTC · claude · 开始任务
- 2026-11-04 13:38 UTC · claude · 方案与核验记录写好：主动发消息统一走工作台 MCP 工具 im_send（以实例身份经 /msg/send_msg 发出，回调照样判断——已在 OpenIM 源码确认单聊、群聊都调用 before 回调）；会话目录 im_list_targets / im_read_messages；附件四种来源与检查；自动回复被拒收时回调记录 + 插件补丁用 enqueueSystemEvent 放回会话（OpenClaw 2026.9.36 文档已确认有这个接口）；发件箱、重试、对账、5002 补发、回调只放行工作台签名的附件；定时消息与唤醒。D1 ~ D10 待人类决定，实施拆为 T-0117 ~ T-0122。
- 2026-11-04 13:38 UTC · claude · status in_progress → done：方案（待批准）与核验记录已写好
