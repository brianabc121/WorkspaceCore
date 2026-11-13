---
id: T-0227
title: 大模型会话桥（一）：会话桥服务 services/llm-bridge（MCP、会话与回合、Codex / Claude / WorkBuddy 驱动、管理接口、镜像与 Compose）与核验记录
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,infra/llm-bridge,docs,.github/workflows,Makefile
depends_on:
created: 2026-11-13
updated: 2026-11-13
---

# T-0227 大模型会话桥（一）：会话桥服务 services/llm-bridge（MCP、会话与回合、Codex / Claude / WorkBuddy 驱动、管理接口、镜像与 Compose）与核验记录

## 目标
落实 `docs/v2/LLM_BRIDGE_PLAN.md` 第 15 节的第 1 ~ 5 项和第 8 项：在锁定版本上核验三家官方命令行，
实现独立部署的会话桥服务（MCP 工具、会话与回合、Codex / Claude / WorkBuddy 驱动、管理接口）、镜像与 Compose。
工作台一侧的模块（登记、权限、工具转发、页面）是后面的任务。

## 验收标准
- [x] 核验记录 `docs/decisions/2026-11-13-llm-bridge-verification.md`，版本写进 `docs/versions.md`
- [x] `services/llm-bridge`：10 个 MCP 工具、会话归属与权限范围、每会话串行队列、每槽位并发上限、幂等发送、超时、取消、重启恢复、一座桥一个账号的检查、密钥打码
- [x] 驱动：Codex（app-server）、Claude 与 CodeBuddy（无界面 stream-json）；`codex.cloud`、`claude.cloud` 是明确的接口桩
- [x] 管理接口：状态、登录 / 退出 / 更换账号、环境、设置、直连调用方令牌、会话与回合查看
- [x] 测试：端到端（fake 平台）、驱动（仿真的官方程序）、对真实 codex / codebuddy 的实测；ruff、mypy 通过
- [x] CI：`ci-llm-bridge` 两个 job（检查与实测、镜像构建与冒烟）在 GitHub 上通过
- [x] `infra/llm-bridge/compose.yaml`、冒烟脚本、服务 README

## 背景 / 约束 / 参考
- 需要真实账号才能核验的部分（登录流程、真实回合、Codex 沙箱在容器里的表现）列在核验记录里，留给验收任务，需要人类登录。
- 本会话里 Debian 软件源被网络策略拦截，完整镜像只能在 CI 上构建。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-13 09:59 UTC · claude · 创建任务（owner: claude）
- 2026-11-13 09:59 UTC · claude · 开始任务
- 2026-11-13 09:59 UTC · claude · 人类 2026-11-13 在对话中要求“根据这份设计方案全面落实这个 mcp 工具”：按方案第 14 节的推荐（D1 ~ D9）实施。codex 的 T-0224 占着 infra、docs/decisions、.github/workflows 等大范围路径；本任务只新增 services/llm-bridge、infra/llm-bridge 和新的文档 / 工作流文件，不改它正在改的文件。
- 2026-11-13 10:35 UTC · claude · 会话桥服务写完：本地 ruff / mypy 通过，pytest 22 通过；对真实 codex 0.169.0、codebuddy 2.184.0 的实测 2 通过（Claude 的实测用例需要显式给 API 密钥，没有运行）。本地没能构建完整镜像（Debian 源被本会话网络拦截，构建时容器还被重启过一次），镜像构建和冒烟检查交给 CI。下一步：看 CI，再做工作台模块（新任务）。
- 2026-11-13 11:10 UTC · claude · status in_progress → done：会话桥服务完成：CI 的 llm-bridge（ruff、mypy、pytest、对真实 codex / codebuddy 的实测）与 image（镜像构建、冒烟检查）两个 job 在提交 19ee032 上通过。需要真实账号的核验项列在核验记录里，留给验收。
