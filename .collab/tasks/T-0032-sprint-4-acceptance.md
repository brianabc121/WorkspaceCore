---
id: T-0032
title: Sprint 4 集成与验收：DoD 实测、A10 / A11 / A12 / A13 / A17 自动化、全栈与浏览器 E2E 纳入 CI、勾选 TASKS.md
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/acceptance,docs/TASKS.md,tests/e2e,.github/workflows/ci-infra.yml,scripts/smoke.sh
depends_on: T-0027, T-0028, T-0029, T-0030, T-0031
created: 2026-10-30
updated: 2026-10-31
---

# T-0032 Sprint 4 集成与验收：DoD 实测、A10 / A11 / A12 / A13 / A17 自动化、全栈与浏览器 E2E 纳入 CI、勾选 TASKS.md

## 目标
T-0027 ~ T-0031 合并后做前后端联调，并按 Sprint 4 的 DoD 实测验收。
通过后勾选 `docs/TASKS.md` 的 S4-01 ~ S4-05，请人类批准进入 Sprint 5。

## 验收标准
- [x] DoD 实测（规范第 13 节）：无未授权文档正文抓取（工作台不向 AFFiNE 发请求，索引里没有文档正文）；搜索结果只显示可见资源；GitHub 限流有清晰降级
- [x] 在 Compose 全栈上自动化 A10（源对象删除后搜索投影标记删除、页面无僵尸引用）、A11（搜索另一工作空间的数据不返回）、A12（`http://169.254.169.254` 等危险地址作为集成地址或收藏链接被拒绝）、A13（失效的 GitHub Token 显示重新连接、不无限重试）、A17（GitHub 429 / 次级限流时遵守退避，不把空列表当正常），并纳入 CI
- [x] CI 里没有真实 GitHub Token：全栈验收用按 T-0025 记录的官方响应格式编写的模拟 GitHub 服务（只用于测试，可注入 429 / 401），并写明它与真实 GitHub 的差异；另提供需要 `TEST_GITHUB_TOKEN` 的可选真实验证
- [x] 浏览器 E2E 纳入 CI：新增收藏 → 关联任务 → 搜索命中 → 删除后搜索不再出现；日历新建 / 编辑 / 删除与首页 agenda；连接 GitHub → 开发者页 → 限流提示；viewer 只读
- [x] `docs/acceptance/sprint-4.md` 逐条列出通过 / 失败 / 未运行及原因（规范第 18 节）
- [x] 勾选 `docs/TASKS.md` 的 S4-01 ~ S4-05；escalate 给人类，请求批准进入 Sprint 5

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
- 2026-10-30 23:36 UTC · claude · 第二部分（一）：T-0030（PR #45）审查无阻塞后合并。新增 tests/e2e/sprint4_browser.cjs（真实后端：配置 AFFiNE 地址与 A12、收藏规范化与关联任务、任务抽屉相关文档、搜索命中、日历新建/编辑与时区、首页最近文档/近期日程、移动端、viewer 只读、A10 删除后不出现、不请求 AFFiNE），本机连续 3 次全部通过，加入 ci-infra.yml。下一步：等 codex 的 T-0031，补 GitHub 连接 / 开发者页 / 搜索框步骤，勾选 TASKS.md S4-01 ~ S4-05。
- 2026-10-30 23:49 UTC · claude · PR #46 第一次 CI 的 Sprint 4 浏览器步骤失败：脚本竞态（字段级错误在输入时就出现，被拦下的那次提交还在校验时脚本已填入合法地址，那次提交带着新值保存，弹窗关闭后第二次点击超时）。改为等表单级 alert（提交校验结束后才出现），并核对只提交一次合法地址；收藏表单同样处理。本机连续 3 次通过。
- 2026-10-31 00:00 UTC · claude · A12 补充实测（本机全栈）：集成地址的各种写法（十进制 / 十六进制 / 八进制 IP、IPv4 映射 IPv6、fe80::、0.0.0.0、回环、内网、带凭据、file/gopher）都返回 422；AFFiNE 地址的十进制 / 十六进制 / 八进制写法被放行（后端只认标准 IP 写法）。已在 app/schemas/knowledge.py 修复（越界到 services/api，归 claude，D-0003），加参数化测试。另记：DNS 重绑定（检查与连接之间再次解析）留到 Sprint 6 安全加固。
- 2026-10-31 00:16 UTC · claude · PR #47 的 CI（ac11646）里 Sprint 1 浏览器验收失败：getByText('其他会话') 是子串匹配，安全页上修改密码表单的说明里也有这四个字；会话列表先渲染出来时就有两个匹配（strict mode 报错），后渲染时则误等到了说明文字。是 T-0017 写的脚本里潜伏的竞态，与本 PR 的改动无关。改为 exact 匹配，本机从空库运行 sprint1_browser.cjs 全部通过。
- 2026-10-31 00:31 UTC · claude · PR #48（codex，T-0031）的 compose 失败在 Sprint 3 浏览器验收“原生修改出现在工作台”：Worker 在测试连接后异步注册 Webhook（00:17:02.260 / .273），脚本 00:17:02.266 就在 Vikunja 里改了任务，事件没有发出；30 秒内又没赶上每分钟一次的对账。是 T-0023 写的脚本里的竞态，与 T-0031 的改动无关。sprint3_browser.cjs 改为先等项目的 Webhook 注册好再做原生修改，本机连续 2 次通过；PR #48 的失败 job 重跑一次。
- 2026-10-31 00:38 UTC · claude · 人类要求给 codex 的指令写进 GitHub issue，免得每次手动转发：已建 issue #50（claude 维护，codex 每次开工先读），CLAUDE.md 记下这条约定。issue 只是传话，任务与球权仍以 .collab 为准（AGENTS.md 第 1 节）。
- 2026-10-31 01:01 UTC · claude · 第二部分完成：T-0031（codex，PR #48）审查无阻塞，经 PR #51 解决日志冲突后合并。sprint4_browser.cjs 补上连接 GitHub → 测试连接与逐仓库检查 → 开发者页（仓库 / Issue 不含 PR / Actions 故障摘要）→ 限流提示（A17）→ 顶栏搜索（按类型分组、跳到日历日期）→ viewer 看开发者页 → 删除后搜索框不再出现；结束时停用自己的 GitHub 集成（本机反复运行时，残留的启用集成会让 fake-github 主机的熔断器打开）。13 步本机连续 3 次通过。报告 docs/acceptance/sprint-4.md 完成，勾选 TASKS.md 的 S4-01 ~ S4-05。按人类 2026-10-30 的预先批准（见本日志 17:27 一条）进入 Sprint 5，不再 escalate；已按 D-0003 拆出 Sprint 5 的 T-0033 ~ T-0040。
- 2026-10-31 01:01 UTC · claude · status in_progress → done：Sprint 4 验收通过：DoD 与 A10 ~ A13、A17 全部通过，接口级 31 项与浏览器 13 步纳入 CI，TASKS.md S4-01 ~ S4-05 已勾选；按预先批准进入 Sprint 5
