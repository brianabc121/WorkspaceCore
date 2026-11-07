# 2026-11-02 在工作台保存并显示 Vikunja、n8n 的登录账号（T-0080）

> 已被取代：Vikunja 登录账号由 T-0082 移除，n8n 登录账号与整个 app-logins 模块由 `docs/decisions/2026-11-07-remove-n8n.md`（T-0149）移除。下文保留原决定与验证，不是当前账号管理功能。

- 状态：accepted（claude，任务范围内的技术决策）
- 关联：`2026-11-02-deploy-connect.md`（第 2.4 条由本决定修改）、`services/api/app/domain/app_logins.py`、
  `apps/web/src/features/app-logins/AppLoginCard.vue`、迁移 `0019`

> 其中 Vikunja 的部分（「项目」页的登录账号卡片、`app = vikunja`）已由 `2026-11-02-remove-vikunja.md`（T-0082）移除；剩余的 n8n 登录账号后续由 T-0149 移除。

## 1. 背景

人类要求（2026-11-02）：在工作台的「项目」页（Vikunja）与「自动化」页（n8n）直接看到这两个管理界面的账号密码，
自己复制粘贴去登录。之前 `deploy.sh connect` 生成的密码只在终端显示一次，工作台不保存（deploy-connect ADR 第 2.4 条）。

自动登录（单点登录）另行讨论：Vikunja 支持 OIDC，n8n 的 OIDC / SAML / LDAP 都要企业版许可证，
本决定只解决“看得到、能复制”。

## 2. 决定

1. **新表 `workspace.app_logins`**（每个工作空间每个应用一条）：用户名明文，密码用 `CredentialBox` 加密，
   只存 `enc:v1` 引用，加密用途绑定 `app_login:<id>:password`（密文挪到别的记录上解不开），纳入密钥轮换。
2. **只有 owner / admin 能读写**，在后端校验（`workspace_admin`）；member、viewer 一律 403，非成员 404，
   页面上也不显示卡片、不发请求。
3. **列表不含密码**：`GET /api/v1/app-logins` 只返回用户名、是否保存了密码、登录页地址与版本。
   查看明文要单独调用 `POST /api/v1/app-logins/{app}/reveal`：
   - 是写请求，要 CSRF Token，跨站页面无法触发；
   - 每次都写审计 `app_login.revealed`（谁、什么时候、哪个应用），审计元数据只有应用名；
   - 响应带 `Cache-Control: no-store`（列表与保存接口也带），浏览器和代理不缓存。
   页面上默认显示圆点，点「显示密码」或「复制密码」才请求；显示的密码 1 分钟后自动遮住，离开页面即清掉。
4. **保存**：`PUT /api/v1/app-logins/{app}`（乐观并发，`app_login.created` / `updated` 审计只记改了哪些字段），
   `DELETE` 删除保存的账号（不影响 Vikunja、n8n 里的账号）。网页上保存时**不去** Vikunja / n8n 核对密码：
   这是给人看的备忘，登录失败就在页面上改。
5. **登录页地址**取自 Vikunja 集成与 n8n 连接的浏览器地址（`public_url`），没有配置时为空。
6. **部署脚本自动保存**：
   - `connect vikunja` / `connect n8n` 登录成功后，用同一个账号密码调用保存（单独的数据库会话，保存失败只提示，
     不影响接入）；
   - 新增 `connect save-login --app vikunja|n8n --username …`（标准输入给密码）：默认先登录一次核对密码
     （Vikunja `POST /api/v2/login` 不对返回 403，n8n `POST /rest/login` 返回 401，见 deploy-connect ADR 第 3 节），
     对了才保存；
   - `connect status` 多输出 `vikunja_login` / `n8n_login`（`saved` / `none`）；`deploy.sh connect` 在组件已接入、
     但还没保存账号时（T-0080 之前接入的）询问一次，输入账号密码后调用 `save-login`。

## 3. 与之前决定的关系

- deploy-connect ADR 第 2.4 条“工作台不保存这两个密码”改为：生成的密码仍在终端显示一次，同时加密保存到工作台，
  只给管理员看。
- Vikunja 核验 ADR 里“不保存用户的 Vikunja 密码”指 Connector 不用密码登录：Connector 仍只用最小权限的 API Token，
  这里保存的密码不参与任何同步或写操作。

## 4. 风险与取舍

- 工作台被攻破（管理员会话被盗）时，攻击者能看到 Vikunja / n8n 的密码。缓解：只有 admin 以上能看、要 CSRF、
  每次查看写审计（审计日志页可查）；n8n owner 建议开两步验证（部署教程已写）。
- 数据库或备份泄露时只有密文；密钥在 `.env` 的 `CREDENTIAL_ENCRYPTION_KEY`，与数据库备份分开保存（部署手册第 6 节）。
- 复制到剪贴板后由浏览器 / 系统管理，工作台无法清除；页面提示“写入审计日志”。

## 5. 验证

- `services/api/tests/test_app_logins_api.py`：权限（viewer、member 403，admin 可以，非成员 404）、数据库里只有密文、
  列表不含密码、查看要 CSRF、`no-store`、版本冲突、删除、审计与日志里没有密码、登录页地址。
- `tests/test_connect_command.py`：接入成功后自动保存、密码不对不保存、`save-login` 先核对密码、覆盖旧密码、`status` 输出。
- `tests/test_contract.py`、`test_credential_rotation.py`（新列参与轮换）、`test_security_matrix.py`（新接口自动覆盖）。
- 前端 `apps/web/tests/app-logins.test.ts` 与 `tests/browser-sprint5.cjs`（显示 / 隐藏、复制、只改变化的字段、
  viewer 看不到也不请求）。
