# OpenClaw Model Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. If the human selects parallel execution, use superpowers:subagent-driven-development. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** OpenClaw通过连接+模型运行，并有可观察、可恢复的配置应用。  
**Architecture:** 绑定是目标，任务是应用动作，远端确认是已应用；原生provider直连，不增加推理网关。  
**Tech Stack:** 现有FastAPI/arq、Node接入助手、锁定OpenClaw镜像、现有密码SSH部署与接入网关。  
**Spec:** ../specs/2026-11-09-model-connections-design.md 与 contract同目录文件。

## Global Constraints

- 依赖MC-0～MC-3，不修改OpenClaw版本或放宽工具策略来绕过适配失败。
- 不把模型Key写进OpenClaw配置、接入包、SSH命令参数、日志或浏览器。
- SecretRef的受控exec不等于允许智能体exec；现有tools.exec/elevated/文件策略保持。
- 同服务器多实例独立项目、目录、cron、配置、任务；不重启整台服务器。
- 已有凭据/授权只在新配置确认后切换；失败/未知不能显示已应用。

## Review Focus

1. 只更新API Key却未应用URL/主模型：MC-5以真实上游请求证明目标生效。
2. v1确认迟于v2目标：MC-4用迟到回报证明不会回滚状态。
3. 两个实例在一台服务器：MC-5证明只变目标容器，另一实例配置/请求不变。
4. 用户自定义其他配置：MC-5证明MCP/IM/工具限制保持。
5. 手动或离线实例：MC-4/5证明pending/manual_required/unknown不被误报成功。

## 文件地图

新增建议：
- services/api/app/domain/agent_model_bindings.py：绑定、版本与应用任务。
- services/api/app/domain/agent_model_config.py：纯配置渲染与局部合并。
- services/api/app/schemas/agent_model_bindings.py：Binding/ApplyJob类型。
- services/api/app/agent_bundle/files/bin/model-config.mjs：受控配置助手。
- services/api/tests/test_agent_model_bindings.py、test_agent_model_config.py。
修改：
- api/v1/agents.py、api/agent_api.py、网关真实白名单位置。
- agent_bundle/__init__.py、files/openclaw.json.template、files/bin/myworkspace-secret-resolver、files/CHECKLIST.md。
- agent_provisioning/__init__.py、既有部署/运维任务接入点。
- tests/test_agents_api.py、test_oc6_config.py、test_oc6_security.py；tests/e2e/新增model_connections_e2e.py或按项目E2E机制新增。

## MC-4：绑定资源和实例认证协议

**Consumes:** MC-1连接/授权、MC-2模型集合、MC-0配置schema。
**Produces:** AgentModelBindingService.put/get/remove/apply/report；契约第3节owner/agent接口。
- put(access, instance_id: UUID, body: ModelBindingPut) -> AgentModelBindingOut
- build_model_config(principal: AgentPrincipal) -> AgentModelConfigOut
- report_applied(principal, body: ModelConfigApplied) -> AgentModelBindingOut

- [ ] 写test_binding_requires_same_workspace_grant_and_allowed_model：跨workspace/未授权/停用/未配置/非法模型/未核验协议均拒绝。
- [ ] 写test_new_deployment_creates_instance_grant_binding_atomically：新实例尚无ID时仍能选择有效连接，事务成功才排部署；失败回滚、幂等重放无重复。\n- [ ] 写test_binding_state_is_desired_not_applied：PUT后pending，不自动写applied_revision；重复apply复用同任务。
- [ ] 写test_old_applied_credential_survives_pending_switch：切换A→B但尚未确认时A仍可用，确认后模型候选只留B；显式撤销/停用立即阻止后续取值，Runner仍按自身有效引用处理。\n- [ ] 写test_old_ack_and_job_cannot_override_new_binding：v1成功回报迟到，v2仍pending；故障回报不泄露内部字段。
- [ ] 写test_agent_cannot_read_another_binding_or_key：实例API只能取自己配置和当前授权连接；路径UUID猜测无效。
- [ ] 写test_disable_revoke_blocks_all_credential_paths：含旧resolver和Runner，区分后续取用拒绝与缓存Key已存在。
- [ ] 实现版本与同实例串行锁；先验证latest revision再写状态，复用已有认证/限流。
- [ ] 将必要新agent路由纳入内外网白名单并测试未扩大其他路径。
- [ ] 运行新绑定测试、现有agent API与安全测试，更新OpenAPI并提交。

## MC-5：配置生成、应用与真实OpenClaw联调

**Consumes:** MC-4绑定/任务/配置接口、MC-0锁定schema与reload方法。
**Produces:** render_managed_model_config(binding,connection)的纯函数；受控配置应用；一机多实例E2E证据。

- [ ] 写test_render_official_and_relay_provider：官方URL原样用于官方目标；中转URL保留自定义base path；两个模型ID均可正确选主模型。
- [ ] 写test_model_config_contains_secretref_not_plaintext：provider_key基于UUID、secret_ref_id基于连接；无Key、无用户字符串shell拼接。
- [ ] 写test_merge_preserves_mcp_im_and_tool_denials：只改受管provider/默认模型，保留未知自定义字段；冲突给预览/明确错误。
- [ ] 写test_apply_atomic_write_validate_and_rollback：先写临时文件，运行已核验的配置校验，再原子替换并应用；失败保留旧配置，结果未知如实标记。
- [ ] 在接入包生成处从绑定注入模型片段，取消所有新原生实例固定openai_api_key的默认值；没有有效绑定的新部署不允许进入启动成功。
- [ ] 更新secret resolver：新连接ref走新认证接口；旧alias走兼容适配，不读取未授权其他连接。
- [ ] 自动部署通过现有密码SSH受控任务应用；手动接入提供新接入包/明确步骤与确认，不伪造远端操作完成。
- [ ] 定时Key reload继续生效；配置助手负责URL/协议/模型更新。不要用Key reload返回0代替配置校验或模型验证。
- [ ] 用锁定真实OpenClaw + 可控假模型服务A/B运行：OpenAI官方协议预设（测试transport/域名映射）、兼容中转、一次模型切换、Key轮换、重启恢复、429、401。
- [ ] 一台测试服务器部署两个实例，各自请求不同模型；切换其中一个后另一个无重启/配置变化。
- [ ] 在原生协议已核验的范围内跑真实OpenClaw测试；未覆盖的协议标blocked并隐藏入口。
- [ ] 跑现有OC6/agent/部署/Runner回归；记录镜像digest、请求目标/模型、脱敏证据、退出码，提交。

## 手动接入说明必须交付

说明如何在目标实例获得更新包、预览配置差异、备份原非敏感配置、运行配置助手与校验、应用/重启、确认实际主模型和回报结果。命令按MC-0核验结果写，禁止凭空编造OpenClaw CLI子命令。
API保存绑定成功但没有远端确认时页面显示待手动应用，不能把下载成功当已应用。

## 运行时阶段命令

~~~bash
cd services/api
uv run pytest tests/test_agent_model_bindings.py tests/test_agent_model_config.py
uv run pytest tests/test_agents_api.py tests/test_oc6_config.py tests/test_oc6_security.py
uv run pytest tests/test_agent_deployment_requests.py tests/test_agent_server_password_jobs.py tests/test_runner_api.py
cd ../..
python3 scripts/collab.py check
~~~

真实OpenClaw E2E入口在MC-5建立后，把确切命令写进验收日志；目前本包不声称该脚本已经存在或已跑通。不使用未给出的生产地址/Key做测试。
