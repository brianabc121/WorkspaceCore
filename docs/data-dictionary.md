# 数据字典

> 状态：Sprint 1 进行中（迁移 0002 已完成）。目标实体清单见 `docs/DEVELOPMENT_SPEC.md` 第 5 节；本文记录**已经迁移**的数据库对象。

## 约定

- 所有自有表放在 `workspace` schema；主键 UUID；时间字段 `TIMESTAMPTZ`（UTC）。
- 所有结构变化都通过 Alembic 迁移（`services/api/migrations/versions/`），并配有测试。
- 约束命名规则见 `services/api/app/models/base.py` 的 `NAMING_CONVENTION`。

## 已迁移对象

| 迁移 | 对象 | 说明 |
|---|---|---|
| `0001` | schema `workspace` | 工作台自有表的命名空间 |
| `0001` | 扩展 `pgcrypto` | `gen_random_uuid()` 等函数 |
| `0001` | 扩展 `pg_trgm` | 中文标题的 `ILIKE` / 三元组模糊搜索（规范第 5.3 节） |

两个扩展在 PostgreSQL 13+ 都是 trusted 扩展，数据库 owner 无需超级用户即可创建。
Alembic 版本表 `alembic_version` 位于 `public` schema。

## 表

### 迁移 `0002`（S1-01）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `users` | `id`、`email`、`password_hash`、`display_name`、`status`、`created_at`、`updated_at` | `email` 唯一且必须小写；`status` ∈ `active` / `disabled`；密码为 Argon2id 哈希 |
| `auth_sessions` | `id`、`user_id`、`token_hash`、`expires_at`、`last_seen_at`、`revoked_at`、`created_at` | 只存会话 Token 的 SHA-256；`token_hash` 唯一；删除用户时级联删除 |
| `external_identities` | `id`、`user_id`、`issuer`、`subject`、`created_at` | `(issuer, subject)` 唯一；为将来的 OIDC 预留 |
| `workspaces` | `id`、`name`、`owner_id`、`created_at`、`updated_at` | `owner_id` → `users`（RESTRICT：工作空间存在时不能删除其 owner） |
| `workspace_members` | `workspace_id`、`user_id`、`role`、`created_at` | 联合主键；`role` ∈ `owner` / `admin` / `member` / `viewer`；随工作空间或用户级联删除 |

ORM 模型与迁移的一致性由集成测试中的 `alembic check` 保证。
