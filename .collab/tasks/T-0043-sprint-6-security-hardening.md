---
id: T-0043
title: Sprint 6 安全加固与安全测试：DNS 重绑定、按集成计数的熔断、规范第 12 节安全测试补齐（S6-05）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api
depends_on: T-0040
created: 2026-10-31
updated: 2026-10-31
---

# T-0043 Sprint 6 安全加固与安全测试：DNS 重绑定、按集成计数的熔断、规范第 12 节安全测试补齐（S6-05）

## 目标
补上 Sprint 4 验收报告第 7 节留到 Sprint 6 的两项安全加固，并按规范第 12 节补齐安全测试，作为 S6-05 安全发布的一部分。

## 验收标准
- [x] DNS 重绑定：Connector 发请求时连接到已经检查过的 IP（而不是让 httpx 连接时再解析一次），同时保留 TLS 的主机名校验与 SNI；有覆盖“检查后解析结果变化”的测试
- [x] 熔断器按集成计数：一个集成持续失败，不影响其他工作空间里指向同一主机的集成（Sprint 4 报告第 7 节、Sprint 5 验收中遇到的情况）；有测试
- [x] 规范第 12 节的安全测试逐项核对并补齐：CSRF、IDOR（跨工作空间读写每类资源）、SSRF、Token 泄露（响应、日志、审计）、权限提升（各角色对每个写接口）、会话撤销；在 PR 里列出每项对应的测试
- [x] `ruff` / `mypy` / `pytest` 全部通过；没有迁移，或迁移有测试

## 背景 / 约束 / 参考
- 规范：第 9 节、第 12 节
- 参考：`docs/acceptance/sprint-4.md` 第 7 节，`docs/acceptance/sprint-5.md` 第 5 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 06:04 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 06:41 UTC · claude · 开始任务
- 2026-10-31 06:53 UTC · claude · DNS 重绑定：OutboundPolicy.check 返回检查过的地址，ConnectorHttp 直接连接它（URL 换成 IP，Host 头保留原 host:port，https 时用 httpcore 1.0.9 的 sni_hostname 扩展让 TLS 证书按主机名校验）；允许的内网主机与 IP 字面量不改写。本机用自建 CA 起 HTTPS 服务实测：连 127.0.0.1、SNI 为 pinned.test 时证书校验通过、服务端收到原 Host；主机名不匹配时 CERTIFICATE_VERIFY_FAILED（Hostname mismatch）。熔断：ConnectorHttp.breaker_scope，Vikunja / GitHub 按 integration:<ID>、n8n 按 automation_connection:<ID> 计数，运维的部署级检查仍按目标服务。规范第 12 节：新增 tests/test_security_matrix.py，按 OpenAPI 枚举全部接口——未登录 401（60 个）、写请求缺 CSRF 403（33 个）、非成员 404（53 个）、viewer 写 403（28 个）；tests/test_outbound_http.py 7 条（修改前 5 条失败）。ruff / ruff format / mypy（app migrations tests scripts）通过；pytest 397 passed, 7 skipped（真实 PostgreSQL + Redis）。
- 2026-10-31 06:53 UTC · claude · status in_progress → done：DNS 重绑定防护、按集成计数的熔断、接口安全矩阵测试完成
