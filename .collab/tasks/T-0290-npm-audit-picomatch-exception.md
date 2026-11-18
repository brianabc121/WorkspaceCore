---
id: T-0290
title: 依赖扫描：npm 公告按条判定，给 picomatch（GHSA-fc5q-39xc-vjpf）登记带到期日的临时例外
owner: claude
status: done
priority: P1
branch: claude/amazing-faraday-6ey29e
paths: scripts/security-scan.sh,scripts/security-exceptions.txt,docs/decisions/2026-11-18-npm-audit-picomatch-exception.md,docs/decisions/2026-10-31-sprint-6-backup-and-scan-verification.md,docs/deployment.md,docs/threat-model.md
depends_on:
created: 2026-11-18
updated: 2026-11-18
---

# T-0290 依赖扫描：npm 公告按条判定，给 picomatch（GHSA-fc5q-39xc-vjpf）登记带到期日的临时例外

## 目标
2026-11-17 起 `ci-security` 的 `deps` job 在所有 PR 和 main 上失败：`apps/web` 的 npm audit 报出 picomatch 的高危公告
GHSA-fc5q-39xc-vjpf（ReDoS，影响 `<=4.0.7`，还没有修复版本）。picomatch 只经 vite、vitest、vue-tsc 这些开发和构建工具引入，
生产的前端镜像只有构建出的静态文件。人类 2026-11-18 同意集成会话推荐的做法：只放过这一条公告、带到期日，上游修复后删掉。

## 验收标准
- [x] `scripts/security-scan.sh deps` 的 npm 部分按 `npm audit --json` 逐条判定：高危及以上、不在有效例外里的失败；npm audit 出错失败；中低危只列出
- [x] 例外登记在 `scripts/security-exceptions.txt`（公告编号、到期日、说明），过期自动失效并写明“例外已到期”
- [x] 只登记 GHSA-fc5q-39xc-vjpf，到 2026-12-18 为止
- [x] 决策记录写明影响核对、到期和后续；部署文档、威胁模型、扫描核验记录跟着更新
- [x] 本机验证：有例外时通过；例外过期、删掉、连不上公告库时都失败

## 背景 / 约束 / 参考
- 编号由本会话按集成者身份在 #235 预留：https://github.com/brianabc121/WorkspaceCore/issues/235
- 决策：`docs/decisions/2026-11-18-npm-audit-picomatch-exception.md`
- Python 依赖（pip-audit）的判定不变。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-18 01:45 UTC · claude · 创建任务（owner: unassigned；编号 T-0290 由本会话按集成者身份在 #235 预留）
- 2026-11-18 01:37 UTC · human · owner unassigned → claude：人类在对话中指派：按集成会话推荐的选项 A 处理 picomatch 公告（只放过这一条、带到期日），并全部合并到 main
- 2026-11-18 01:37 UTC · claude · 开始任务
- 2026-11-18 01:37 UTC · claude · 做完：npm 公告按条判定、例外文件与到期日、决策记录和文档。本机实测 scripts/security-scan.sh deps：有例外时通过（pip-audit 无漏洞；apps/web 例外 GHSA-fc5q-39xc-vjpf，另列 2 条中危只报告；tests/e2e 0）；把到期日改成昨天时失败并写明“例外 2026-11-17 已到期”；删掉例外时失败；npm_config_registry 指向不可达地址时两个项目都失败（npm audit 出错）。
- 2026-11-18 01:37 UTC · claude · status in_progress → done：picomatch 临时例外到 2026-12-18，验收标准全部勾选
