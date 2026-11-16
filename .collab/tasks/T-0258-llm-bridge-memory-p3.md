---
id: T-0258
title: 大模型客户端：会话纪要、提问前带上知识库、存进知识库、外发开关（压缩与知识库方案第 3 期）（V2）
owner: claude
status: done
priority: P1
branch: claude/amazing-faraday-6ey29e
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/app/domain/knowledge_base.py,services/api/app/domain/knowledge_views.py,services/api/app/domain/knowledge_tools.py,services/api/app/models/knowledge_base.py,services/api/app/schemas/knowledge_base.py,services/api/app/schemas/knowledge_tools.py,services/api/app/api/v1/knowledge_base.py,services/api/migrations/versions/20261116_0075_llm_bridge_memory.py,services/api/tests/test_module_llm_bridge_memory.py,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/features/kb,apps/web/src/services/schema.d.ts,apps/web/tests,docs/api-contract.md
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0258 大模型客户端：会话纪要、提问前带上知识库、存进知识库、外发开关（压缩与知识库方案第 3 期）（V2）

## 目标
`docs/v2/LLM_BRIDGE_MEMORY_PLAN.md` 第 3 期（方案第 5、6 节）：压缩时丢掉的重点留进工作台的知识库，提问前用上知识库里已有的内容。
人类 2026-11-16 决定：会话纪要默认自动写入；知识库默认允许发给外部平台；提问时不自动带笔记，由人或智能体挑。

## 验收标准
- [x] 会话纪要（5.2）：不调模型，管理器按记录给素材（`GET /admin/sessions/{id}/digest`），工作台按固定几栏排好写进知识库“会话纪要”文件夹，类型 digest（未确认）；
      3 个以上回合并且压缩 / 归档 / 24 小时没有新回合时写；同一个会话合并进同一篇、每次一个版本；放进项目库 / 智能体自己的库 / 公共库
- [x] 5.3 的保护从第一天起都有：逐回合密钥扫描、每篇 20 KB、每个知识库的纪要上限（满了停写并通知管理员）、默认不进检索候选和图谱、
      `knowledge_search` 要明确 `type=digest`、智能体不能写纪要、人改过 / 转正 / 删掉的不再自动写、随保留期清（可关）、审计 `llm_bridge.digest_written`
- [x] 管理器设置里纪要的两个开关（默认开）；纪要页（`/llm_bridge?view=digests`）和会话信息里能看、能关、能现在整理、能转成正式笔记、能删除
- [x] 相似提示（6.1）：输入时在知识库和以前的会话里找相近的，只在工作台里做；智能体新建会话时结果里带相似的旧会话；`llm_bridge_send` 说明里提示先 `knowledge_search`
- [x] 挑选笔记带上（6.2）：聊天页“参考知识库”（最多 3 篇）、智能体 `knowledge_notes`（最多 5 篇，按它的读权限）；固定格式的“参考资料”包装、合计 6000 字截断、
      只给模型看（记录里是原话和“参考了”）、发出前密钥扫描、审计 `llm_bridge.knowledge_attached`
- [x] 知识库的“允许发给外部大模型平台”开关（默认开），关掉后候选里标出、带它被拒绝
- [x] 回答上的“存进知识库”：选知识库、改标题和内容，保存前提示同一个知识库里相近的笔记（接在后面或另存），内容带出处链接
- [x] 智能体读会话、取回合时结果里带纪要编号（它读得到时）
- [x] 管理器、工作台后端、前端测试覆盖以上各项；迁移 0075 可升级可回退

## 背景 / 约束 / 参考
- 方案第 5、6、7 节；`docs/v2/KNOWLEDGE_PLAN.md` 3.8 第 7 条（不自动塞进提示词）不改。
- “带着摘要另开会话”触发的纪要属于第 2 期。纪要里“结论和决定”“没解决的问题”两栏等第 4 期把平台压缩时写的摘要并进来。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 07:23 UTC · claude · 创建任务（owner: claude）
- 2026-11-16 07:24 UTC · claude · 开始任务
- 2026-11-16 07:28 UTC · claude · 第 3 期完成。和 T-0257（第 1 期）放在同一个 PR #225 里：CI 一次接近一小时，方案也要求第 1、3 期先一起做。本地：管理器 81 passed；工作台 ruff / mypy（586 个文件）/ openapi 检查通过，test_module_llm_bridge_memory（6）、test_module_llm_bridge_chat、test_module_llm_bridge、test_integration（迁移与模型一致）、test_knowledge_*（72）通过；前端 lint / typecheck / vitest 887 passed / build 通过。paths 与 T-0224（codex，已暂停）重叠，只改 llm_bridge 模块和知识库里为纪要、外发开关必须改的几处。
- 2026-11-16 07:28 UTC · claude · status in_progress → done：第 3 期完成，验收标准全部勾选
