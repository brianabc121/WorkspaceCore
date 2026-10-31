---
id: T-0044
title: Sprint 6 安全扫描、性能基准与完整端到端 Smoke Test，纳入 CI（S6-05）
owner: claude
status: todo
priority: P1
branch:
paths: scripts,tests/e2e,tests/perf,.github/workflows,Makefile,docs/deployment.md
depends_on: T-0041
created: 2026-10-31
updated: 2026-10-31
---

# T-0044 Sprint 6 安全扫描、性能基准与完整端到端 Smoke Test，纳入 CI（S6-05）

## 目标
S6-05：把安全扫描、性能基准和覆盖全部模块的端到端 Smoke Test 纳入 CI，发布前能用一条命令确认整个系统可用、没有已知的高危漏洞，
性能达到规范第 12 节的测试目标（或如实记录差距）。

## 验收标准
- [ ] 安全扫描纳入 CI（按 T-0041 的选择）：Python 与 npm 依赖、密钥泄露、容器镜像；高危以上失败；已知误报在文件里登记理由
- [ ] 性能基准脚本（`tests/perf/` 或 T-0041 选定的位置）：在 Compose 全栈上测基础 API 与缓存命中的首页的 P95，输出报告；结果与规范目标（300 ms / 500 ms）对比，未达标时写明原因
- [ ] 完整端到端 Smoke Test：一条命令（例如扩展 `make smoke`）覆盖登录、首页、任务、知识、日历、开发者、运维、通知、自动化等页面与接口的基本可用性，纳入 CI
- [ ] CI 总时长保持在合理范围（例如把扫描放进独立的 job）；本机与 CI 共用同一套脚本

## 背景 / 约束 / 参考
- 依赖：T-0041
- 规范：第 12 节，第 13 节 Sprint 6，第 18 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 06:04 UTC · claude · 创建任务（owner: claude）
