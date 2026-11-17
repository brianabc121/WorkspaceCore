---
id: T-0288
title: 大模型客户端“对话”页：会话列表的状态标签与详情状态改正；Claude 可选具体模型和推理强度
owner: claude
status: done
priority: P2
branch: wip/llm-client
paths: apps/web/src/modules/llm_bridge/chat,apps/web/src/modules/llm_bridge/llm_bridge.css,apps/web/tests/llm_chat.test.ts,services/llm-bridge/bridge/drivers/cli_stream.py,services/llm-bridge/bridge/drivers/fake.py,services/llm-bridge/bridge/engine.py,services/llm-bridge/tests,tests/e2e/llm_chat_browser.cjs,docs/v2/LLM_CHAT_PLAN.md,docs/decisions/2026-11-13-llm-bridge-verification.md
depends_on:
created: 2026-11-17
updated: 2026-11-17
---

# T-0288 大模型客户端“对话”页：会话列表的状态标签与详情状态改正；Claude 可选具体模型和推理强度

## 目标
人类 2026-11-17 在对话里提的六项改动中的两项（另外四项见 T-0289）：

1. 会话列表给每个会话加状态标签（进行中 / 已完成）；会话详情里回合已经做完了还写“进行中”，要改正。
2. 新建 Claude 会话时只有 sonnet / opus / haiku 三个“最新”，不能选具体版本，也不能选推理强度；要像官方客户端一样能选。

## 验收标准
- [x] 会话列表第二行右边写状态：有回合在回答或排队“进行中”，做完了“已完成”，归档的“已归档”；会话详情的状态用同一个规则
- [x] “更多”菜单里“回到进行中的会话”改为“回到未归档的会话”，不再用“进行中”指没归档
- [x] Claude 的模型取自锁定版本 Claude Code 2.1.323 的模型菜单：四个最新别名（Sonnet、Opus、Fable、Haiku）+ 具体版本（Opus 5.5、Sonnet 5.5、Fable 5.2、Haiku 5.5 和更早的），各带推理强度（low ~ max；Opus 4.6 / Sonnet 4.6 没有 xhigh；Haiku 4.5、Opus 4.1 没有）
- [x] 选了推理强度时驱动加 `--effort <level>`（只给 Claude；CodeBuddy 没核对过，不传）；管理器新建会话时检查强度是所选模型支持的
- [x] 新建会话、多模型同问的推理强度写中文名（低 / 中 / 高 / 超高 / 最高）；同问里同一模型不同强度可以各开一栏
- [x] 核验记录（`docs/decisions/2026-11-13-llm-bridge-verification.md` 第 3 节）、`docs/v2/LLM_CHAT_PLAN.md` 更新；管理器 ruff / mypy / pytest、前端 lint / typecheck / vitest 通过

## 背景 / 约束 / 参考
- 编号由集成会话在 #235 预留（人类同一次对话的六项改动分成 T-0288、T-0289 两个任务，放在同一个 PR 里）。
- 核验方式：从 npm 下载锁定版本的 `@anthropic-ai/claude-code-linux-x64@2.1.323`，只解包，在二进制里读 `--model`、`--effort` 的帮助文本和内置的 `model_selector_config`；`tests/test_live.py` 新增一项在 CI 里用装好的程序核对 `--help`。
- MCP 管理器源码变了，镜像标签跟着变：已部署的管理器要“重新部署”升级后才有具体模型和推理强度。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-17 18:01 UTC · claude · 创建任务（owner: unassigned；编号 T-0288 由集成会话在 #235 预留）
- 2026-11-17 18:01 UTC · human · owner unassigned → claude：人类在对话中指派：大模型客户端六项改动（会话状态标签、Claude 模型与推理强度）
- 2026-11-17 18:01 UTC · claude · 开始任务
- 2026-11-17 18:02 UTC · claude · 做完：会话列表的状态标签（进行中 / 已完成 / 已归档），会话详情的状态改正，“回到进行中的会话”改为“回到未归档的会话”；Claude 模型取自锁定版本 2.1.323 的模型菜单（四个最新别名 + 具体版本，各带推理强度），驱动按 --effort 传，管理器校验强度；推理强度写中文名，同问里同一模型不同强度可以各开一栏。管理器 ruff、mypy、pytest 107 passed / 4 skipped（含 test_live 的 --help 核对，用下载的 2.1.323 二进制本地跑过）；前端 lint、typecheck、vitest 84 个文件 924 个测试、build、check:api 通过。T-0224 的 paths 宽，collab check 报交叠；它的分支 claude/cool-wright-99qbbg 不改这些文件。
- 2026-11-17 18:02 UTC · claude · status in_progress → done：对话页状态标签与 Claude 模型、推理强度完成，验收标准全部勾选
