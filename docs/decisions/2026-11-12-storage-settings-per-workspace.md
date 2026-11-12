# 知识库图片存储改为在网页上按工作空间设置（T-0223）

- 日期：2026-11-12
- 状态：accepted（人类 2026-11-12 在对话中要求“直接改到可以通过 web 端的 workspace 进行配置”）
- 相关：`docs/v2/TIMELINE_PLAN.md` 第 4.2 节（原设计）、`docs/deployment.md` 第 19 节、
  `docs/decisions/2026-11-04-timeline-kb-upgrade-verification.md` 第 3 节（R2 / OSS 的 S3 兼容接口核验）

## 背景

T-0103 把知识库图片（与 T-0140 的常驻环境快照）的存储写成部署级配置：`.env` 的 `FILE_STORAGE`、`FILE_STORAGE_REPLICA`、
`R2_*`、`OSS_*`，由 `deploy.sh connect` 在 SSH 终端里问。人类建好 Cloudflare R2 桶后希望直接在网页上配置，
并且按工作空间分开（人类 2026-11-06 也要求需要用户设置的东西尽量放在网页上）。

## 决定

1. **按工作空间存在数据库**：表 `workspace.storage_settings`（迁移 0060），一行一个工作空间：主存储（`local` / `r2` / `oss`）、
   副本（`none` / `local` / `r2` / `oss`，不能与主存储相同）、R2（账户 ID、桶名、辖区、Access Key ID、Secret 密文）、
   OSS（地域、桶名、AccessKey ID、Secret 密文）、`version`。没有这一行就是本机、不复制（与原来的默认相同）。
2. **页面与权限**：网页“设置 → 图片存储”（`/settings/storage`，侧栏“管理”分组）；owner 修改，admin 只读，member 看不到；
   接口 `GET / PUT /api/v1/settings/storage` 在服务端同样校验。它是宿主的核心设置（与邮件设置、中转站管理一样），
   不做成插拔式模块：知识库图片与快照离不开它，不能被停用。
3. **密钥**：用部署的 `CREDENTIAL_ENCRYPTION_KEY`（`CredentialBox`）加密，用途绑定 `storage_settings:<工作空间>:<字段>`；
   接口只返回 `secret_set`；`null` 保持、空字符串清除；凭证轮换（`credential_rotation.py`）一起处理这两列；
   审计 `storage.settings_updated` 只记改了的字段名与换了密钥的存储名。
4. **地址只能是官方域名**：不接受任意 endpoint。账户 ID（字母数字）、辖区（小写字母）、地域、桶名（小写字母、数字、短横线）
   校验后拼成 `https://<账户>[.<辖区>].r2.cloudflarestorage.com`（路径风格）与 `https://<桶>.s3.oss-<地域>.aliyuncs.com`
   （虚拟托管风格），地域去掉可选的 `oss-` 前缀。所以设置页没有 SSRF 面。
   部署级的 `R2_ENDPOINT`、`R2_REGION`、`OSS_ENDPOINT` 只保留给测试指向 Garage。
5. **保存前自检**：对选中的主存储与副本各写、读、删一个探测对象（与运维页的自检相同），不通过返回
   `422 STORAGE_CHECK_FAILED` 与各自原因，设置不变；通过后把结果写进运维页“最近一次自检”。
6. **在用不能清空**：还存着本工作空间图片（主存储或待复制 / 已复制的副本）或未删除快照的存储，连接信息不能清空
   （`409 STORAGE_IN_USE`），否则那些文件读不到。换主存储不搬旧文件：旧图片仍从写入时的存储读。
7. **Worker 按工作空间**：复制、清理、每日自检都按每个工作空间自己的设置取存储；运维页的待复制数量也按工作空间。
8. **去掉部署级配置**：`.env.example`、`compose.yaml` 不再有这些变量，`deploy.sh connect` 不再问，`connect storage-check` 删除。
   还没有生产数据（人类确认不需要向后兼容），不把旧 `.env` 的值迁移进数据库。

## 影响

- 单张上限与配额（`FILE_MAX_MB`、`FILE_QUOTA_MB`）仍是部署级设置。
- 每个工作空间可以用不同的桶；同一个桶给多个工作空间也可以（对象 key 带工作空间 ID）。
- 真实 R2 / OSS 账号仍未在 CI 里验收（没有测试桶）；第一次在设置页保存时的自检就是实际的连通性验收。
