---
id: T-0058
title: OC-6 安全测试：反 Prompt Injection、越权与跨实例、绕过审批、接入网关与 OpenIM 拦截、回调伪造（S6-03）
owner: claude
status: todo
priority: P1
branch:
paths: services/api/tests,tests/e2e
depends_on: T-0055, T-0057
created: 2026-10-31
updated: 2026-10-31
---

# T-0058 OC-6 安全测试：反 Prompt Injection、越权与跨实例、绕过审批、接入网关与 OpenIM 拦截、回调伪造（S6-03）

## 目标
补齐 V2 的安全测试（方案第 7 节，对应 S6-03 的反 Prompt Injection）。

## 验收标准
- [ ] Prompt Injection：任务描述、Issue、其他实例的群消息里诱导删除或外泄，都无法绕过审批
- [ ] 越权：跨工作空间、跨实例、凭证与入口错配、被拦截的 OpenIM 接口、伪造回调
- [ ] 绕过审批：改参数、重复批准、过期批准、AI 自批、聊天里回复“同意”都无效

- [ ] OC-0 ADR F4：实例用 `message` 工具私聊其他实例会被拒收；F9：插件升级前的发布包审查流程

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
