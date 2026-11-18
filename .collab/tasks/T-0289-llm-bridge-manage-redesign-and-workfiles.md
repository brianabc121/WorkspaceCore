---
id: T-0289
title: 大模型客户端“管理器与账号”页按效果图改版（平台与账号、运行设置、新增环境弹窗），开发环境里浏览会话的工作目录
owner: claude
status: done
priority: P2
branch: claude/amazing-faraday-6ey29e
paths: apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests/llm_bridge.test.ts,services/llm-bridge/bridge/app.py,services/llm-bridge/bridge/engine.py,services/llm-bridge/bridge/store.py,services/llm-bridge/bridge/workfiles.py,services/llm-bridge/tests/test_workfiles.py,services/api/app/modules/llm_bridge,services/api/app/domain/audit_labels.py,services/api/openapi.json,services/api/tests/test_module_llm_bridge.py,tests/e2e/llm_bridge_browser.cjs,tests/e2e/llm_chat_browser.cjs,docs/api-contract.md,docs/v2/LLM_BRIDGE_PLAN.md,docs/guides/mcp-manager-deploy.md
depends_on: T-0286
created: 2026-11-17
updated: 2026-11-18
---

# T-0289 大模型客户端“管理器与账号”页按效果图改版（平台与账号、运行设置、新增环境弹窗），开发环境里浏览会话的工作目录

## 目标
人类 2026-11-17 在对话里给了五张效果图（另外两项见 T-0288）：

1. “平台与账号”按图 1 重排：运行概览在标签上面，“全部重新检查”，网络异常提示条，账号卡片（登录状态、网络状态、账号、登录方式、使用额度、近 5 小时用量、管理登录 / 重新检查、“…”），连接诊断收起。
2. “运行设置”按图 2：运行控制、额度恢复后自动继续、会话与保留、高级设置四张卡片，底部“取消更改 / 保存设置”。
3. “开发环境”按图 4：能查看环境里的文件夹和文件。
4. “新增环境”按图 5 改成弹窗。

人类在对话里选定：文件来自会话的工作目录（选环境、再选它的会话，默认最近一个）；允许 owner / admin 下载原文件。

## 验收标准
- [x] 运行概览（进行中、排队中的回合数、版本、平台并发）放在详情的标签上面；右上角“全部重新检查”（重新读状态并重测连通性）
- [x] 平台与账号：网络异常提示条（“查看诊断”展开连接诊断）；账号卡片按效果图，退出登录、更换账号、向平台查一次在“…”里；连通性详情和通道表收进“连接诊断”；原有的登录方式、逐步进度、订阅风险确认、控制的平台都保留
- [x] 运行设置：四张卡片，所有原有设置都在（自动压缩的位置、单条指令字数上限在高级设置里按需展开），校验和提交的内容不变
- [x] 开发环境：环境列表 + 文件夹树、会话的工作目录（路径、搜索、刷新、列表 / 网格）、文件预览（Markdown 可切换预览 / 源码，内容已脱敏）、下载原文件；新增、修改、删除环境在弹窗里
- [x] 管理器：按环境列会话、列目录、预览、下载；只读，只认管理器建的会话目录，不许 ..、解析符号链接后不能出去；预览最多 512 KB 并脱敏，下载最多 50 MB
- [x] 工作台：四个转发接口（admin），下载记审计 `llm_bridge.work_file_downloaded`；重新生成 `openapi.json` / `schema.d.ts`
- [x] 测试：管理器 ruff / mypy / pytest，工作台 ruff / mypy / llm_bridge 相关测试 / export_openapi --check，前端 lint / typecheck / vitest / build / check:api；浏览器验收跟着改

## 背景 / 约束 / 参考
- 编号由集成会话在 #235 预留。基于 PR #251（T-0286）的改动开发，等它合并后提交。
- 和 PR #252（T-0287，集成会话的 Claude 网页授权登录）改了同样的文件（`BridgeAccounts.vue`、`LoginProgress.vue`、`api.ts`、`llm_bridge.test.ts`、`llm_bridge_browser.cjs`、管理器的 `app.py` / `engine.py`）：等它合并后从最新 main 合并，冲突按 #252 的功能放进新布局。
- MCP 管理器源码变了，镜像标签跟着变：已部署的管理器要“重新部署”升级后才能浏览文件。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-17 18:01 UTC · claude · 创建任务（owner: unassigned；编号 T-0289 由集成会话在 #235 预留）
- 2026-11-17 18:01 UTC · human · owner unassigned → claude：人类在对话中指派：大模型客户端六项改动（管理器与账号页按效果图改版、开发环境浏览文件、新增环境弹窗）
- 2026-11-17 18:01 UTC · claude · 开始任务
- 2026-11-17 18:02 UTC · claude · 做完：运行概览放到标签上面、全部重新检查；平台与账号按效果图（网络提示条、账号卡片、连接诊断收起）；运行设置四张卡片与底部操作条；开发环境三栏（环境列表 + 文件夹树、会话工作目录的文件、预览与下载），新增 / 修改环境的弹窗。管理器新增 bridge/workfiles.py 与四个接口（只读、限定在会话工作目录、预览脱敏、下载 ≤ 50 MB），工作台转发接口与下载审计 llm_bridge.work_file_downloaded，重新生成 openapi.json / schema.d.ts。管理器 ruff、mypy、pytest 107 passed / 4 skipped；工作台 ruff、mypy、export_openapi --check、llm_bridge 四个测试文件与 test_audit_queries 58 passed；前端 lint、typecheck、vitest 84 个文件 924 个测试、build、check:api 通过。Vite + Playwright（模拟接口）截图核对 1920 / 1440 和弹窗。浏览器验收（llm_bridge_browser.cjs、llm_chat_browser.cjs）跟着改，以 CI 为准。待 #251、#252 合并后合进最新 main（和 #252 改了同样的文件）再推送。
- 2026-11-17 18:02 UTC · claude · status in_progress → done：管理器与账号页按效果图改版、开发环境浏览文件完成，验收标准全部勾选（浏览器验收以 CI 为准）
- 2026-11-18 01:58 UTC · claude · 合并 main 60cc147（含 #251、#252、#253）：cli_stream.py 同时保留推理强度和网页授权登录，BridgeAccounts.vue 按新布局接上 #252 的授权码输入；main 新增的连通性用例先展开“连接诊断”。合并后：管理器 ruff / mypy / pytest 116 passed 4 skipped；工作台 ruff / mypy / openapi 最新，Postgres 16 + Redis 上 llm_bridge 全部测试文件和 test_contract、test_audit_queries 126 passed；前端 lint、typecheck、vitest 84 个文件 926 个测试、build、check:api 通过；两个浏览器验收脚本 node --check 通过，实际运行以 CI 为准。
- 2026-11-18 02:08 UTC · claude · 处理 PR #254 的 Codex 审查：1）P1 检查路径和打开文件之间的竞态：workfiles 改为从工作目录的文件描述符开始逐级 O_NOFOLLOW 打开，符号链接由管理器展开、只认工作目录以内的，类型 / 大小检查与读取用同一个已打开的文件，下载改为从它流式发送（StreamingResponse，不再按路径重新打开）；2）P2 预览截断处的私钥块：截断时去掉末尾被切开的词，没闭合的私钥块遮到结尾。新增测试 6 个（三个截断位置的私钥、截断的令牌、里外符号链接与循环、检查后换成外链仍读原文件），先在旧代码上确认失败 5 个，新代码上管理器 ruff / mypy / pytest 122 passed 4 skipped（1 个 ResourceWarning 来自已有的网页授权登录测试，改动前就有）；工作台下载用例通过。
