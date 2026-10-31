# 交给 Codex 的 V2 启动指令

请先阅读 `AGENTS.md`、`DEVELOPMENT_SPEC.md`、`AGENT_ARCHITECTURE_V2.md`、`TASKS.md`、`AGENT_TASKS_V2.md`。本项目已从通用工作台升级为 My Workspace V2.0 Agent Control Plane。

请先盘点现有代码和 Sprint 完成状态，不覆盖现有文件或删除功能。如果 Sprint 0–5 基础尚未完成，先执行最小依赖；有关 Agent 的开发先执行 `AGENT_TASKS_V2.md` 的 V2-S0。

当前本轮只要求：
1. 列出代码现状、依赖、未实现模块及风险。
2. 对 OpenClaw Gateway / Codex App Server 当前锁定版本的接口进行核验；如无可运行实例，写适配器契约和待核验项，不猜测接口。
3. 形成 V2-S0 ADR：Provider 能力矩阵、版本、认证、会话、事件、审批与取消机制、隔离和回滚方案。
4. 给出下一阶段 V2-S1 的准确文件清单和测试方案。

不要在本轮实现全部模块。不得擅自放宽沙箱/权限、开启公网 Agent WebSocket、提交密钥或自动推送/合并代码。
