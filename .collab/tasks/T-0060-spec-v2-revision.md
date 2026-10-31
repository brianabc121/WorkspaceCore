---
id: T-0060
title: 规范修订：按 V2 改造方案更新 DEVELOPMENT_SPEC、TASKS 的 S6-01 ~ S6-03 与 AGENTS 第 8 节（需人类批准后合并）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: docs/DEVELOPMENT_SPEC.md,docs/TASKS.md,docs/AGENTS.md
depends_on:
created: 2026-10-31
updated: 2026-10-31
---

# T-0060 规范修订：按 V2 改造方案更新 DEVELOPMENT_SPEC、TASKS 的 S6-01 ~ S6-03 与 AGENTS 第 8 节（需人类批准后合并）

## 目标
把已采纳的改造方案写进人类负责的规范文件（方案第 11 节）。按 D-0004 第 3 节，修改人类负责的文件需要人类批准后才能合并。

## 验收标准
- [x] `docs/DEVELOPMENT_SPEC.md` 按方案第 11 节修订（第 1、2、3 ~ 4、5、6.1、8、9、13、15 节）
- [x] `docs/TASKS.md` 的 S6-01 ~ S6-03 改写，S6-04 ~ S6-06 不动；`docs/AGENTS.md` 第 8 节追加 V2 补充规则
- [x] PR 经人类批准后合并（本 PR 合并即表示已批准）

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 16:30 UTC · claude · 开始任务
- 2026-10-31 16:32 UTC · claude · 起草完成：DEVELOPMENT_SPEC 修订第 1.1 ~ 1.3、2、3、4、4.2、5.1、6.1、8.1、9.2、9.3、11.1、13、15、16 节（开头加 V2 修订说明）；TASKS 改写 S6-01 ~ S6-03（S6-04 ~ 06 不动）；AGENTS 第 8 节追加 V2 补充规则。docs/DEVELOPMENT_SPEC.docx 未同步（资料包也注明 Word 版不同步）。按 D-0004 第 3 节，等人类批准后合并
- 2026-10-31 16:32 UTC · claude · status in_progress → done：规范修订已起草；PR 经人类批准合并后生效
- 2026-10-31 17:09 UTC · human · 人类在对话中批准合并 PR #75（“可以合并”）
