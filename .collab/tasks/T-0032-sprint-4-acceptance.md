---
id: T-0032
title: Sprint 4 集成与验收：DoD 实测、A10 / A11 / A12 / A13 / A17 自动化、全栈与浏览器 E2E 纳入 CI、勾选 TASKS.md
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/acceptance,docs/TASKS.md,tests/e2e,.github/workflows/ci-infra.yml,scripts/smoke.sh
depends_on: T-0027, T-0028, T-0029, T-0030, T-0031
created: 2026-10-30
updated: 2026-10-30
---

# T-0032 Sprint 4 集成与验收：DoD 实测、A10 / A11 / A12 / A13 / A17 自动化、全栈与浏览器 E2E 纳入 CI、勾选 TASKS.md

## 目标
T-0027 ~ T-0031 合并后做前后端联调，并按 Sprint 4 的 DoD 实测验收。
通过后勾选 `docs/TASKS.md` 的 S4-01 ~ S4-05，请人类批准进入 Sprint 5。

## 验收标准
- [ ] DoD 实测（规范第 13 节）：无未授权文档正文抓取（工作台不向 AFFiNE 发请求，索引里没有文档正文）；搜索结果只显示可见资源；GitHub 限流有清晰降级
- [ ] 在 Compose 全栈上自动化 A10（源对象删除后搜索投影标记删除、页面无僵尸引用）、A11（搜索另一工作空间的数据不返回）、A12（`http://169.254.169.254` 等危险地址作为集成地址或收藏链接被拒绝）、A13（失效的 GitHub Token 显示重新连接、不无限重试）、A17（GitHub 429 / 次级限流时遵守退避，不把空列表当正常），并纳入 CI
- [ ] CI 里没有真实 GitHub Token：全栈验收用按 T-0025 记录的官方响应格式编写的模拟 GitHub 服务（只用于测试，可注入 429 / 401），并写明它与真实 GitHub 的差异；另提供需要 `TEST_GITHUB_TOKEN` 的可选真实验证
- [ ] 浏览器 E2E 纳入 CI：新增收藏 → 关联任务 → 搜索命中 → 删除后搜索不再出现；日历新建 / 编辑 / 删除与首页 agenda；连接 GitHub → 开发者页 → 限流提示；viewer 只读
- [ ] `docs/acceptance/sprint-4.md` 逐条列出通过 / 失败 / 未运行及原因（规范第 18 节）
- [ ] 勾选 `docs/TASKS.md` 的 S4-01 ~ S4-05；escalate 给人类，请求批准进入 Sprint 5

## 背景 / 约束 / 参考
- 依赖：T-0027 ~ T-0031 都合并之后才开始；接口级验收可以先于前端完成（同 T-0023 的两段做法）
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 13 节（Sprint 4 DoD）、第 15 节（A10 ~ A13、A17）、第 18 节
- Sprint 1 ~ 3 的验收脚本继续在 CI 里运行，不能因为本 Sprint 的改动失败

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 16:43 UTC · claude · 创建任务（owner: claude）
- 2026-10-30 17:27 UTC · claude · 人类在 2026-10-30 的对话中启动长期任务（定期检查 codex 的提交并持续落实设计文档的全部功能，直到完成），并对“Sprint 交界怎么办”选择了“验收通过就自动进入”：视为对 Sprint 5、Sprint 6 的预先批准（D-0004 第 3 节“进入下一个 Sprint”的例外由人类本人放开）。修改协作协议、不可逆的数据迁移、需要人类提供凭据或决定的事项仍然先问人类。
- 2026-10-30 18:36 UTC · claude · 开始任务
- 2026-10-30 18:57 UTC · claude · 第一部分（接口级）完成：tests/e2e/sprint4_acceptance.py 31 项（DoD、A10 ~ A13、A17 主限流与次级限流）本机连续 3 次全部通过，已加入 ci-infra.yml；模拟 GitHub 为 tests/e2e/fake_github_server.py（复用 services/api/tests/fake_github.py，加了 base 参数）。越界改动（都是 claude 的目录，D-0003）：compose.yaml 给 worker 加 egress 网络并传入 GITHUB_API_URL——修复 T-0028 的缺陷：worker 只在内部网络里，周期同步解析不到 api.github.com（本机实测确认）；docs/deployment.md 第 2、10 节同步更新。下一步：等 codex 的 T-0030 / T-0031 合并后写 sprint4_browser.cjs，补报告第 2.2、3 节，勾选 TASKS.md S4-01 ~ S4-05。
- 2026-10-30 19:19 UTC · claude · PR #44 第一次 CI（compose）失败：.env 里写的 GITHUB_API_URL 被 GitHub Actions 在 shell 里设置的同名变量覆盖（Compose 插值时 shell 优先），api 访问了真实 api.github.com，模拟 Token 被拒（token_rejected），模拟服务收到 0 个请求。本机在 shell 里设置 GITHUB_API_URL 复现；改为 .env 用 WORKSPACE_GITHUB_API_URL（compose 映射为容器内的 GITHUB_API_URL），同样环境下 31 项全部通过。Sprint 1 ~ 3 的验收在那次 CI 里都通过了。
