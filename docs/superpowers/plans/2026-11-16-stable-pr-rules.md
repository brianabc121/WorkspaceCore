# 稳定 PR 协作规则发布计划

**Goal:** 按人类已批准的方案，在首页 README 与代理入口发布一致、可执行的规则，并通知 Claude。

**Architecture:** README 提供首页规则，D-0008 保存完整约定；AGENTS / CLAUDE / 协作手册统一引用。人工队列以 GitHub issue #235 记录，自动化明确列为后续实现。

**Tech Stack:** Markdown、现有协作校验脚本、GitHub PR / Issues。

**Spec:** 人类本次指派与 `.collab/decisions/D-0008-stable-pr-integration.md`。

## 约束

- 本次仅文档，不更改业务代码、workflow、编号解析器或 task 状态集合。
- 不重复申请已经取得的规则修改批准，不宣称自动化已实现或 Claude 已读。
- 保留历史决策与已提交日志，明确新规则的替代范围。

## 实施与验证

- [x] 更新 README 首页、D-0008、AGENTS / CLAUDE 与协作手册，修正过时的暂停及合并说明。
- [x] 复核当前人工执行、后续自动化、相关检查失败处理及唯一集成会话的边界。
- [ ] 运行协作校验、相对链接 / 格式与差异检查，完成独立复核，等待相关密钥检查。
- [ ] 按纯文档门禁发布到 main，更新 issue #235 的固定提交链接并通知 Claude。

不为人工说明编写镜像实现的测试，也不重跑与纯文档无关的产品全量测试。
