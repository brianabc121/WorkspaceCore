---
id: T-0260
title: 大模型客户端：压缩时的钩子与固定带着的笔记（压缩与知识库方案第 4 期）（V2）
owner: claude
status: done
priority: P1
branch: claude/amazing-faraday-6ey29e
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/tests/test_module_llm_bridge_memory.py,services/api/tests/test_contract.py,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests,docs/AGENTS.md,docs/api-contract.md,docs/decisions/2026-11-16-llm-bridge-context-verification.md,docs/v2/LLM_BRIDGE_MEMORY_PLAN.md,docs/v2/README.md
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0260 大模型客户端：压缩时的钩子与固定带着的笔记（压缩与知识库方案第 4 期）（V2）

## 目标
`docs/v2/LLM_BRIDGE_MEMORY_PLAN.md` 第 4 期（4.3“压缩时别丢重点”、6.3“一直带着的参考资料”）：
用官方程序的压缩钩子拿到平台本来就要写的摘要，并进会话纪要、另开会话时直接用；新建会话时可以固定带着几篇笔记，压缩后仍在。
人类 2026-11-16 决定：钩子先按文档做、带开关，在真实账号上验证后再默认打开。

## 验收标准
- [x] 管理器：固定的钩子脚本（压缩前输出“要保留什么、摘要按四栏写”，压缩后把 `compact_summary` 存下来，出错不阻止压缩）；
      经 `--settings` 只给 Claude / CodeBuddy 的回合带上；设置 `hooks_enabled` 默认关；摘要存进 `compaction_summaries`，纪要素材里给出
- [x] 工作台：纪要的“结论和决定”“没解决的问题”并进平台写的摘要（按四栏拆，拆不开整段放进“结论和决定”，像有密钥的不用）；
      带着摘要另开会话时有平台的摘要就直接用（`summary_from=compaction`，不再先问一次）；管理器设置页上的开关（旧版本的管理器不显示）
- [x] 固定带着的笔记：新建会话（聊天页、智能体 `llm_bridge_session_create` / `llm_bridge_send` 新建时）的 `pinned_notes`，最多 5 篇、合计 4000 字；
      检查外发开关、读权限、密钥；对话会话随系统提示带上，开发会话写进记忆文件有标记的一段；会话信息里只显示标题；记审计；已有的会话不能再加
- [x] 文档：核验记录第 5 节、`docs/api-contract.md`、`docs/AGENTS.md` 例外九补 `pinned_notes`、方案的实施情况（第 11 节）
- [x] 管理器、工作台后端、前端测试覆盖以上各项

## 背景 / 约束 / 参考
- 方案 4.3、6.3、第 8、9 节；核验记录 `docs/decisions/2026-11-16-llm-bridge-context-verification.md`。
- 没有任何平台的测试账号：钩子的实际行为没跑过，所以默认关；仍未核实的项目列在核验记录第 5 节。
- 4.3 第三行“压缩后放回参考资料”改为不依赖钩子（系统提示 / 记忆文件），理由见核验记录第 5 节。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 08:26 UTC · claude · 创建任务（owner: claude）
- 2026-11-16 08:31 UTC · claude · 开始任务
- 2026-11-16 08:39 UTC · claude · 第 4 期完成。管理器：钩子脚本 bridge/hooks.py（PreCompact / PostCompact，开关 hooks_enabled 默认关，只给 Claude / CodeBuddy），compaction_summaries 表，固定带着的参考资料（对话会话随系统提示、开发会话写记忆文件）。工作台：纪要并进平台摘要、另开会话直接用平台摘要（summary_from）、pinned_notes（聊天页与智能体）、设置页开关。本地：管理器 91 passed / 3 skipped，ruff / mypy 通过；工作台 ruff / format / mypy（586 文件）/ openapi --check 通过，test_module_llm_bridge_chat / memory（12）/ llm_bridge / contract / integration 87 passed，llm_bridge* + knowledge* + contract 173 passed；前端 lint / typecheck / vitest 893 passed / build 通过。
- 2026-11-16 08:39 UTC · claude · status in_progress → done：第 4 期完成，验收标准全部勾选；钩子默认关，待真实账号验证
