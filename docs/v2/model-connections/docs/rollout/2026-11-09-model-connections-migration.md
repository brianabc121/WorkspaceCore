# 旧密钥迁移、启用和回退手册

本手册供Claude实施后演练与发布使用。以下CLI是要求新增的工具，不是当前主干已有命令。本包没有执行数据库迁移或连接外部模型服务。

## 1. 迁移不等于复制一份明文

推荐扩展式迁移：新增连接/目录/绑定/检查/授权元数据，credential_secret_id引用原AgentSecret。
不复制密文到新secret_id，不解密导出CSV，不把Key写入迁移日志；原context、版本、记录、Runner引用仍然有效。
在新管理入口中，模型Key的管理业务归属连接；旧技术表不是另一个用户可并行编辑的Key库。

历史API Key全部有迁移入口，但不等于全部可自动当模型Key：
- model_api_key：创建待完善连接，保留名称/说明/密钥和旧授权。
- api_token / generic：创建待归类导入项；用户通过分类接口归为model后补URL/协议/模型纳管，或标为non_model放入兼容管理；映射和secret均不换ID，重新归类也不重复创建。
- 不可解密：credential_configured=false，标needs_review，保留原记录等待补Key/修复加密配置；不写假迁移成功。
- 非模型项也在新页面可找到、编辑/轮换和查记录；不要让用户失去原Token入口。

## 2. 必须区分的状态

导入连接：
configuration_state=draft；migration_state=needs_configuration或needs_classification；没有新的AgentModelBinding。
enabled可以继承为true以保留已存在的旧授权取用，但这不表示可用于新部署。原生取用要求enabled && ready && 当前目标/过渡已应用绑定 && 对应连接授权。
用户显式停用以后，统一拒绝该受管凭据的后续旧resolver、新resolver与Runner取用，不能因“兼容”绕过。
旧运行时尚未确认切换时，实例状态是legacy_active；不是applied。\n归类non_model不删除治理映射、不重建已撤销的Grant、不自动enabled=true；旧resolver/Runner仍检查映射的停用状态。撤销某实例的连接授权同步撤销该secret对它的legacy Grant，恢复必须owner显式操作。

不要在迁移默认值中把enabled设为false后又无条件执行停用校验，那会立刻切断所有旧模型。导入不授予任何旧授权之外的访问权限。

## 3. 新增CLI接口

在app.commands.migrate_model_connections建立互斥模式：
- --workspace-id UUID --dry-run：只读盘点，返回将创建/已映射/待归类/缺元数据/不可解密/引用数；无数据库写操作。
- --workspace-id UUID --apply --batch-size 200：显式执行幂等导入。
- --workspace-id UUID --report：只读对账，不输出Key/密文。
禁止默认全workspace执行；必须显式workspace；需要全库迁移由发布流程逐workspace调用。
每批事务200条；唯一约束(workspace_id,legacy_secret_id)保证并行与重跑不重复。
保存每条映射的阶段与错误类别；错误回滚当前批，不损伤已提交批。重复运行继续未完成项。

未来实施命令（测试库先替换变量，切勿直接拿生产库验证）：
~~~bash
export TARGET_WORKSPACE_ID='替换为专用测试工作空间UUID'
cd services/api
uv run python -m app.commands.migrate_model_connections --workspace-id "$TARGET_WORKSPACE_ID" --dry-run
uv run python -m app.commands.migrate_model_connections --workspace-id "$TARGET_WORKSPACE_ID" --apply --batch-size 200
uv run python -m app.commands.migrate_model_connections --workspace-id "$TARGET_WORKSPACE_ID" --report
~~~

工具JSON输出必须包括mode、workspace_id、scanned、mapped、already_mapped、needs_configuration、needs_classification、unreadable、failed和每批结果。不输出API Key、密文或原始异常正文。
dry-run可在受控服务内解密以检查可读性，但不变更记录、导出值或保存解密结果。

## 4. 发布顺序

1. 从最新主干核对迁移head、冲突任务与已部署OpenClaw版本；审阅ADR。
2. 使用现有加密备份机制做备份与隔离恢复演练；核对当前/previous encryption keys可用，不把密钥放进本包。
3. 在测试库做扩展Alembic迁移，确保不drop旧表，不触发远端调用。
4. 部署兼容后端：旧接口仍工作，新接口先限制为测试workspace；提供独立feature gate，具体配置接入遵循项目现有设置机制。
5. dry-run与对账，审核分类，apply后再次运行证明幂等。
6. 部署管理页；入口可以对旧项显示待补全，不创建伪造的URL或模型。首次补空URL/协议不受旧Grant的在用地址修改限制，不要求先撤销旧授权。
7. 在测试workspace补齐官方与中转两类连接，运行元数据/可选推理检测；低成本测试要显式知晓会产生请求。
8. 给一个智能体授予连接、选择真实模型，预览配置差异，触发受控apply或更新手动接入包。
9. 以真实OpenClaw发到测试服务的目标/模型/版本确认成功，再把实例从legacy_active转为applied。
10. 逐实例扩展，保留未迁移实例的alias与Runner注入；一机多实例逐个切换。
11. 全部验收后把新页面作为主入口，旧路由redirect；旧兼容API仍不在本次drop。

目标连接与已应用连接分开保存；切换在途时保留仍获授权的旧已应用连接取用，成功确认再退休其模型候选；显式停用、撤权或解除绑定优先拒绝，不能用过渡兼容绕过。\n\n同一模型secret被多个实例授权时，不因其中一个切换就清理所有旧alias；每实例单独确认。
任何步骤产生未知完成状态先查询任务/远端配置，禁止盲目再执行部署或重建实例。

## 5. 验收对账

迁移前后必须核对：
- 原secret、当前value_version、原Grant(alias)、Access历史记录数量和ID可追溯。
- 旧model_api_key/api_token/generic各自的导入数、待完善数、非模型数、失败数。
- 每旧secret最多一个迁移映射；连接授权数没有无故放大。
- 目标绑定、实际应用、pending/manual/failed/unknown分开统计。
- 旧resolver/Runner读权限：原允许继续、原禁止继续禁止；显式停用/撤权后拒绝。
- CredentialBox旧值仍可解密；加密主密钥轮换后新旧路径都可用。
- 服务商429/401不会把配置和模型集合清空。

## 6. 回退

场景A：仅新增表/导入元数据，无新绑定应用。
- 关闭新页面feature gate，恢复旧入口；保留新增表/映射供排查，不在生产直接downgrade删除数据。
- 原secret/grant/access没有改ID，旧代码可继续读取；核对新版对旧API的适配是否需要保留兼容后端。
- 若发生托管凭据的新修改，不能直接回退到不理解托管规则的旧后端；优先回退前端，保留新兼容后端。

场景B：已给部分实例应用新连接。
- 从每实例记录取前一个非敏感配置快照，验证后恢复该实例的模型字段；不重建整台服务器或清理其他实例文件。
- 恢复旧授权和alias前确认其仍存在且未被明确撤销；不能用回退越过管理员停用决定。
- 新绑定/任务保留历史，标回退/未应用并可重试，不把未知结果改成成功。

场景C：Key已轮换，旧密文已销毁或服务商旧Key已失效。
- 旧配置快照不包含Key，无法凭快照恢复旧Key。
- 通过新页面录入有效Key或切到已验证的新连接；此时文档与UI不能承诺无条件一键还原。
- 平台删除/轮换不是服务商撤销操作，若需撤销到服务商处理。

本次不包含移除旧表、清空历史取用记录、批量强制重启、不可逆的密文清理。之后若要物理收缩数据库，必须另立任务并遵守不可逆迁移审批边界。
