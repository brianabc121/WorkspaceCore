# 代码基线与接入点

本包核对基线：4fbf8637a59a06c78acc3fececfe26b26ad398dc，PR #180（智能体服务器/OpenClaw拆页、密码连接、独立初始化、一机多实例部署）。生成日期2026-11-09，展示时区Asia/Shanghai。
本地工作树有早先未提交改动；本次读取统一使用git show origin/main:<path>，没有把旧分支的脏文件当事实源。源码指纹见source-manifest.json，实施前重新fetch并比较。

## 已确认事实与修改位置

| 接入点 | 实际路径 | 本次影响 |
|---|---|---|
| 密钥页 | apps/web/src/pages/SecretsPage.vue | owner说明、旧路由与兼容入口 |
| 密钥CRUD/取用记录UI | apps/web/src/features/agents/SecretsWorkspace.vue | 新管理入口/兼容凭据重整 |
| 类型/分页hook | apps/web/src/features/agents/queries.ts | 接入新连接hook，避免两个Key事实源 |
| 智能体详情与授权 | apps/web/src/features/agents/AgentsWorkspace.vue、InstanceCard.vue | 新主模型面板，高级非模型授权保留 |
| 自动部署抽屉 | apps/web/src/features/agents/AgentDeployDrawer.vue | 新部署连接/模型选择 |
| 侧栏/路由 | apps/web/src/app/navigation.ts、router.ts | 新入口与旧URL redirect |
| 原凭据模型 | services/api/app/models/agent.py | AgentSecret/Version/Grant/Access继续作为内部设施 |
| 密钥业务服务 | services/api/app/domain/agents.py | 迁移映射、轮换/兼容取用统一校验 |
| 管理接口 | services/api/app/api/v1/agents.py | 旧接口读写兼容；新连接router另外注册 |
| 实例认证/路由 | services/api/app/api/agent_api.py | 新配置/取值/应用回报与网关白名单 |
| 原密钥类型 | services/api/app/schemas/agents.py | 不删除generic/api_token或旧resolve格式 |
| 凭据加密 | services/api/app/domain/credentials.py | AES-256-GCM与用途绑定复用 |
| 主密钥轮换 | services/api/app/domain/credential_rotation.py | 现有FIELDS覆盖agent_secret_versions；改变存储才扩展 |
| Runner注入 | services/api/app/domain/runner_hosts.py | resolve_env_secrets同时依赖AgentSecretGrant，必须回归 |
| 安全HTTP | services/api/app/integrations/http.py | OutboundPolicy、固定解析IP、无自动跳转 |
| 模型模板 | services/api/app/agent_bundle/files/openclaw.json.template | 目前固定openai/openai_api_key，需要从binding生成 |
| 接入包生成 | services/api/app/agent_bundle/__init__.py | BundleInput和_openclaw_json接入实际连接配置 |
| 受控resolver | services/api/app/agent_bundle/files/bin/myworkspace-secret-resolver | 当前POST /v1/secrets/resolve，改为连接ref适配并兼容alias |
| 定时reload | services/api/app/agent_provisioning/__init__.py | cron_lines当前每10分钟刷新secret，不能当URL/模型配置应用 |
| 锁定镜像 | services/api/app/agent_bundle/files/compose.yaml | openclaw/openclaw:2026.9.36 + 已锁digest，不浮动升级 |
| 新闻独立AI | services/api/app/modules/news/config.py | 独立模型配置，不在此次旧库迁移范围 |
| 既有回归 | services/api/tests/test_agents_api.py、test_runner_api.py、test_credential_rotation.py、test_oc6_config.py、test_oc6_security.py | 必跑，不能只新增mock单测 |

## 当前行为

AgentSecretOut只含名称、类型、版本、授权和时间，不含明文。create/edit/rotate/delete及取用日志已存在。
list_secrets只有cursor/limit；现有accesses API支持instance_id、secret_id、result；旧前端只提供实例/结果筛选。
rotate_secret销毁旧版本密文并新增当前版本，因此不能把“配置回退”写成“取回旧Key”。
SecretRef resolver通过受控exec只做取Key，不等于让AI获得任意exec权限。
新密码SSH登录是服务器功能，和模型Key不是同一凭据域。
现有schema.d.ts是自动生成文件，产品实施应从新OpenAPI生成，不手工维护。

## 本包没有验证的内容

- 没有连接生产平台或用户的服务器/模型服务。
- 没有核验新增的上游模型协议、OpenClaw配置字段或reload子命令。
- 没有运行产品lint/test/build/迁移；本包是开发文档。
- 没有创建正式.collab任务、提交代码、推送或创建PR。
- 视觉样例的Key状态、迁移数字、模型可用性均不是用户实际数据。

这些工作分别在MC-0、MC-5、MC-8实施时留下真实证据。本包只验证文件与压缩包完整性。
