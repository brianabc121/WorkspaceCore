---
id: T-0058
title: OC-6 安全测试：反 Prompt Injection、越权与跨实例、绕过审批、接入网关与 OpenIM 拦截、回调伪造（S6-03）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/tests,tests/e2e
depends_on: T-0055, T-0057
created: 2026-10-31
updated: 2026-11-01
---

# T-0058 OC-6 安全测试：反 Prompt Injection、越权与跨实例、绕过审批、接入网关与 OpenIM 拦截、回调伪造（S6-03）

## 目标
补齐 V2 的安全测试（方案第 7 节，对应 S6-03 的反 Prompt Injection）。

## 验收标准
- [x] Prompt Injection：任务描述、Issue、其他实例的群消息里诱导删除或外泄，都无法绕过审批
- [x] 越权：跨工作空间、跨实例、凭证与入口错配、被拦截的 OpenIM 接口、伪造回调
- [x] 绕过审批：改参数、重复批准、过期批准、AI 自批、聊天里回复“同意”都无效

- [x] OC-0 ADR F4：实例用 `message` 工具私聊其他实例会被拒收；F9：插件升级前的发布包审查流程

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
- 2026-11-01 11:26 UTC · claude · status todo → in_progress：开工
- 2026-11-01 11:27 UTC · claude · 先盘点了已有覆盖（前面任务已覆盖：读工具把外部文本放在 untrusted、A14、If-Match、过期、二次认证、入口与凭证错配、网关白名单与用户 ID 校验、回调路径与来源网段、重复回调、跨实例取密钥与查记录），再补缺口。新增 services/api/tests/test_oc6_security.py（14 项）：读到诱导删除/外泄的任务描述后，实例经 MCP 发起的删除、修改、建书签都只是待审批，没有读密钥/审批/外部请求类工具；乙在群里 @ 甲的话不会被当成甲的审批记录来源（只认管理员的消息）；数据库里的参数被改过时批准也不执行（ARGUMENTS_TAMPERED）；凭证密钥轮换前的待审批仍能批准；同一条并发批准只执行一次；批准请求不接受多余字段、缺 If-Match 返回 422；二次认证连续 5 次错误后锁定（429，正确密码也不行）；实例凭证调审批接口 401、MCP 没有审批工具、发起时夹带 approved/password 字段被拒；member / viewer 对真实记录批准和拒绝都是 403；单聊、群聊里回复“同意”或带记录 ID 不改变记录；同一个人在另一个工作空间看不到、处理不了这条记录；停用实例、关闭总开关、收回写权限后批准不执行；没有会话读不到记录。test_im_gateway.py 的拦截清单扩充到 16 个接口（改会话、按序号拉消息、建群/解散/转让、撤回删除、管理员与强制下线等），并断言都没有转发。新增 test_oc6_config.py：F4 部署配置打开了单聊/群聊发送前回调且失败即拒（FAILEDCONTINUE=false）；F9 插件版本与 sha256 在 docs/versions.md、CHECKLIST.md、infra/openclaw/README.md、OC-0 ADR、PLUGIN_VERSION 一致，安装命令锁定版本。F9 审查流程：新增 tests/e2e/openclaw_plugin_review.py（核对锁定 sha256、静态扫描、候选版本差异；用 0.3.2 当候选复现了 OC-0 记录的唯一差异），ci-security 的 deps job 每次运行；infra/openclaw/README.md 写明升级步骤。agent_ingress_acceptance.py 的 /mcp 检查改为 initialize 成功（原来还按 OC-5 之前的 404），在本会话的 e2e 栈上 13 项全部通过。越界修改（说明原因）：services/api/app/domain/ai_tools.py 与 credentials.py——测试发现批准时不复核参数哈希，改数据库就能改执行内容，补上执行前校验（HMAC，支持轮换前的旧密钥）；OC-5 ADR 第 3 节记录这条规则；.github/workflows/ci-security.yml 加插件审查步骤；infra/openclaw/README.md 写升级流程。未补：回调内容与 OpenIM 拉取结果不一致的专门用例（投影本来就以拉取结果为准，已有 test_callbacks_trigger_projection_by_seq 覆盖）
- 2026-11-01 11:36 UTC · claude · status in_progress → done：OC-6 安全测试完成：验收标准全部勾选；完整后端测试 565 passed / 14 skipped；接入入口验收 13 项通过；插件审查脚本通过
