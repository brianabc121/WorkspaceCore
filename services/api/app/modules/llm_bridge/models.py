"""大模型会话桥模块的表（docs/v2/LLM_BRIDGE_PLAN.md 第 11 节）。

- llm_bridge_bridges：工作空间登记的会话桥（一座桥 = 一个独立部署的容器 = 每个平台一个账号）。
  管理令牌用凭证密钥加密后存引用，不回传浏览器。
- llm_bridge_grants：按实例的权限（例外九）：没有这一行或 enabled 为 false 的实例
  看不到也调用不了工具。bridge_ids 限定实例能用哪几座桥（NULL 表示都能用）。
- llm_bridge_chat_groups / llm_bridge_chat_group_members：聊天页的“多模型同问”
  （docs/v2/LLM_CHAT_PLAN.md 第 4.3 节）。一组同问可以跨会话桥，所以登记在工作台；
  每个成员就是某座桥上的一个普通会话，消息内容仍然只在会话桥里。
- llm_bridge_installs：工作台部署出去的 MCP 管理器（docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 5 节）：
  部署在哪台智能体服务器或容器服务器上、网络怎么接、专用的转发密钥。自己存一份服务器的
  地址和主机公钥：服务器从工作台移除、改密码、吊销密钥都不影响已经部署的管理器。
- llm_bridge_deploy_jobs：部署、重新部署、重启、卸载任务，各一条记录，带步骤和脱敏后的日志。
  管理令牌、服务器密码、sudo 密码都不在这两张表里。
- llm_bridge_remote_containers：MCP 管理器在容器服务器上的远程开发容器
  （docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md）：对应一个容器服务器环境（owner_kind=llm_bridge）；
  镜像、资源、网络等设置。各平台在容器里的登录记在管理器里。
- llm_bridge_remote_procs：管理器的远程进程号 ↔ 容器服务器的运行（转发输出、补发结束用）。不存输出。
- llm_bridge_sessions：会话索引（docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 3.4）：每个管理器上有哪些会话、
  谁建的、出处、回合数和用量。不含对话内容。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LlmBridge(Base):
    __tablename__ = "llm_bridge_bridges"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(Text)
    token_ref: Mapped[str] = mapped_column(Text, server_default=text("''"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    # 会话索引同步到了管理器的哪个变更序号（llm_bridge_sessions，jobs.py）
    index_rev: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    # 已经通知过管理员的额度暂停：平台 → 暂停到什么时候（同一次暂停只通知一次）
    pause_notified: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    # 管理器设置里会话纪要的开关（enabled、follow_retention），同步会话索引时带回来
    # （docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2、5.3）；没同步过为空，按默认（开）处理
    digest_settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LlmBridgeSession(Base):
    """会话索引：每个 MCP 管理器上有哪些会话（docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 3.4）。

    不含任何提问和回答——对话内容只在管理器里。由后台任务按管理器的变更序号增量同步，
    用来跨管理器列出和筛选会话、汇总用量。标题是管理器里脱敏之后的那份。"""

    __tablename__ = "llm_bridge_sessions"
    __table_args__ = (
        UniqueConstraint("bridge_id", "session_id"),
        CheckConstraint("owner_kind IN ('agent', 'user', 'external')", name="owner_kind"),
        Index("ix_llm_bridge_sessions_recent", "workspace_id", "updated_at"),
        Index("ix_llm_bridge_sessions_owner", "workspace_id", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    bridge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.llm_bridge_bridges.id", ondelete="CASCADE")
    )
    session_id: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, server_default=text("''"))
    status: Mapped[str] = mapped_column(Text)  # active / archived
    origin: Mapped[str] = mapped_column(Text)  # bridge（经管理器新建）/ external（官方程序里已有）
    # 谁建的：agent（owner_id 是实例的 id）/ user（owner_id 是 user:<用户 id>）/ external（没有）
    owner_kind: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_label: Mapped[str] = mapped_column(Text, server_default=text("''"))
    # 出处：建会话时工作台带过去的（实例或人，当时在做的任务）
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    busy: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    turns_total: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    turns_failed: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    turns_interrupted: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    tokens: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    # 平台压缩过几次；对话内容按保留期清掉的时间（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2、5.3）
    compactions: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_turn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LlmBridgeGrant(Base):
    __tablename__ = "llm_bridge_grants"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE"), primary_key=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    # 总开关：打开后实例能看到工具、能查看自己的会话
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # 新建会话、发送、取消、归档
    can_write: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # 看到账号下全部会话的标题等（不含内容）
    can_account: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # 读取、续发不属于自己的会话
    can_takeover: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # 允许的平台；空列表表示都允许
    platforms: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # 允许用的会话桥（id 的列表）；NULL 表示都允许（包括以后登记的），空列表表示一个都不允许
    bridge_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    # 允许用的开发环境（管理器里的环境编号）；空列表表示一个都不能用（T-0247）
    environment_ids: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LlmChatGroup(Base):
    __tablename__ = "llm_bridge_chat_groups"
    __table_args__ = (
        # 同一个人用同一个 request_id 再建一次，得到的是同一组（重试不会建出两组）
        UniqueConstraint("workspace_id", "created_by", "request_id"),
        Index("ix_llm_bridge_chat_groups_listing", "workspace_id", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(Text)
    # 谁建的；用户被删除后保留当时的显示名
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    request_id: Mapped[str] = mapped_column(Text)
    # active / archived
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LlmChatGroupMember(Base):
    __tablename__ = "llm_bridge_chat_group_members"
    __table_args__ = (UniqueConstraint("group_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        # 按命名约定生成的名字超过 PostgreSQL 的 63 个字符，这里直接给一个短的
        ForeignKey(
            "workspace.llm_bridge_chat_groups.id",
            ondelete="CASCADE",
            name="fk_llm_bridge_chat_group_members_group",
        )
    )
    position: Mapped[int] = mapped_column(Integer)
    # 会话桥的登记被删除后置空：这一栏只剩当时的名字，不能再读内容
    bridge_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.llm_bridge_bridges.id", ondelete="SET NULL"), nullable=True
    )
    bridge_name: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 会话桥上的会话编号；这个目标的会话没建成时为空，原因在 error
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str] = mapped_column(Text, server_default=text("''"))


class LlmBridgeInstall(Base):
    __tablename__ = "llm_bridge_installs"
    __table_args__ = (
        UniqueConstraint("bridge_id"),
        Index("ix_llm_bridge_installs_server", "workspace_id", "server_id"),
        Index("ix_llm_bridge_installs_runner_host", "workspace_id", "runner_host_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    # 对应的管理器登记；登记删除时这一行一起删除
    bridge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.llm_bridge_bridges.id", ondelete="CASCADE")
    )
    # agent_server（智能体服务器）/ runner_host（容器服务器）
    target_kind: Mapped[str] = mapped_column(Text)
    # 来源服务器；服务器从工作台移除后置空，下面那份服务器信息保留
    server_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_servers.id", ondelete="SET NULL"), nullable=True
    )
    runner_host_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.runner_hosts.id", ondelete="SET NULL"), nullable=True
    )
    server_name: Mapped[str] = mapped_column(Text)
    # 部署时的服务器信息：SSH 通道用它登录（不依赖服务器页面上的登记）
    address: Mapped[str] = mapped_column(Text)
    ssh_port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str] = mapped_column(Text)
    host_key: Mapped[str] = mapped_column(Text)
    # 这次安装的编号（12 位十六进制）：目录、Compose 项目名、标签、内部网络里的名字都由它得出
    slot: Mapped[str] = mapped_column(Text)
    install_dir: Mapped[str] = mapped_column(Text)
    # same_host（加入工作台的内部网络）/ tunnel（只监听服务器的本机地址，工作台经 SSH 通道访问）
    mode: Mapped[str] = mapped_column(Text)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 只能转发到 127.0.0.1:<port> 的专用密钥：私钥是加密引用，公钥是装在服务器上的那一行里的
    tunnel_key_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    tunnel_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    platforms: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # 部署的构建文件版本（内容摘要）
    source_tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 这台服务器上操作 Docker 要 sudo 密码（页面据此先问密码）
    needs_sudo: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    deployed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL"), nullable=True
    )
    deployed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LlmBridgeDeployJob(Base):
    __tablename__ = "llm_bridge_deploy_jobs"
    __table_args__ = (
        # 同一个 request_id 再提交一次，得到的是同一个任务
        UniqueConstraint("workspace_id", "request_id"),
        # 一台服务器同时只跑一个 MCP 管理器的任务（两类服务器各一个索引；空值不参与）
        Index(
            "uq_llm_bridge_deploy_jobs_running",
            "server_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        Index(
            "uq_llm_bridge_deploy_jobs_running_runner",
            "runner_host_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        Index("ix_llm_bridge_deploy_jobs_listing", "workspace_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    # deploy / redeploy / restart / uninstall
    kind: Mapped[str] = mapped_column(Text)
    # running / succeeded / failed / interrupted
    status: Mapped[str] = mapped_column(Text, server_default=text("'running'"))
    # agent_server / runner_host
    target_kind: Mapped[str] = mapped_column(Text)
    # 部署到哪台服务器；服务器被移除后置空，保留当时的名字
    server_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_servers.id", ondelete="SET NULL"), nullable=True
    )
    runner_host_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.runner_hosts.id", ondelete="SET NULL"), nullable=True
    )
    server_name: Mapped[str] = mapped_column(Text)
    # 登记成功后指向那座会话桥；登记被删除后置空
    bridge_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.llm_bridge_bridges.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text)
    platforms: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    slot: Mapped[str] = mapped_column(Text)
    # 检查服务器之后才知道
    mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    install_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL"), nullable=True
    )
    steps: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    log: Mapped[str] = mapped_column(Text, server_default=text("''"))
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LlmBridgeTurnWatch(Base):
    """智能体发送时选了“结束后叫我”的回合（docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 4.1 的 A1）。

    后台任务隔十几秒问一次管理器；回合结束就给这个实例排一次唤醒，记上 done_at。
    被打断后有回合接着做（插入的消息、额度恢复后的续跑）时，turn_id 改成接着做的那一回合。"""

    __tablename__ = "llm_bridge_turn_watches"
    __table_args__ = (
        UniqueConstraint("bridge_id", "first_turn_id"),
        Index(
            "ix_llm_bridge_turn_watches_open",
            "bridge_id",
            postgresql_where=text("done_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    bridge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.llm_bridge_bridges.id", ondelete="CASCADE")
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE")
    )
    session_id: Mapped[str] = mapped_column(Text)
    first_turn_id: Mapped[str] = mapped_column(Text)  # 智能体发出去的那一回合
    turn_id: Mapped[str] = mapped_column(Text)  # 现在盯着的回合（可能是接着做的那一回合）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)


DIGEST_STATES = ("active", "off", "converted", "edited", "deleted", "purged")


class LlmBridgeDigest(Base):
    """会话纪要（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2）：一个会话一篇，
    写在知识库的“会话纪要”文件夹里。

    内容由工作台按管理器的记录整理（不调模型），每次合并进同一篇、留一个版本。state：
    active 照常更新；off 有人关掉了这个会话的纪要；converted 已转成正式笔记；
    edited 人改过纪要（不再自动覆盖）；deleted 纪要被删进了回收站；
    purged 会话内容按保留期清掉，纪要随之放进回收站。"""

    __tablename__ = "llm_bridge_digests"
    __table_args__ = (
        UniqueConstraint("bridge_id", "session_id"),
        CheckConstraint(f"state IN {DIGEST_STATES}", name="state"),
        Index("ix_llm_bridge_digests_note", "note_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    bridge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.llm_bridge_bridges.id", ondelete="CASCADE")
    )
    session_id: Mapped[str] = mapped_column(Text)
    note_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.knowledge_notes.id", ondelete="SET NULL"), nullable=True
    )
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.knowledge_spaces.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    # 写进去的时候会话有几个回合、压缩过几次：有新的才再写
    turns_written: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    compactions_seen: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # 最近一次为什么写：compaction / archived / idle / continued
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    written_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LlmBridgeRemoteContainer(Base):
    """远程开发容器（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 5、11 节）。"""

    __tablename__ = "llm_bridge_remote_containers"
    __table_args__ = (
        CheckConstraint("image_kind IN ('builtin', 'template', 'custom')", name="image_kind"),
        UniqueConstraint("bridge_id", "name"),
        UniqueConstraint("runner_environment_id"),
        UniqueConstraint("workspace_id", "request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    bridge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.llm_bridge_bridges.id", ondelete="CASCADE")
    )
    # 新建时的幂等号：用同一个再提交一次，得到的是同一个容器
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 容器服务器上的环境；被删掉（或者从容器服务器页面以外的途径没了）时置空，页面显示“容器已不在”
    runner_environment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.runner_environments.id", ondelete="SET NULL"), nullable=True
    )
    host_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.runner_hosts.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text)
    image_kind: Mapped[str] = mapped_column(Text)
    # template：模板名；custom：镜像名
    image: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.runner_templates.id", ondelete="SET NULL"), nullable=True
    )
    # 建容器时的命令行版本（和管理器不一致时提示更新镜像）
    cli_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # 资源（cpus、memory_mb、disk_gb）、runtime、network、domains、secrets、idle_stop_minutes
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class LlmBridgeRemoteProc(Base):
    """管理器的一个远程进程（回合、登录、作业、准备会话目录）对应的容器服务器运行。"""

    __tablename__ = "llm_bridge_remote_procs"
    __table_args__ = (
        CheckConstraint("purpose IN ('turn', 'login', 'job', 'prepare')", name="purpose"),
        UniqueConstraint("bridge_id", "proc_id"),
        Index("ix_llm_bridge_remote_procs_run", "run_id"),
        Index(
            "ix_llm_bridge_remote_procs_unsent",
            "bridge_id",
            postgresql_where=text("exit_sent = false"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    bridge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.llm_bridge_bridges.id", ondelete="CASCADE")
    )
    container_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.llm_bridge_remote_containers.id", ondelete="CASCADE")
    )
    proc_id: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.runner_runs.id", ondelete="SET NULL"), nullable=True
    )
    purpose: Mapped[str] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_sent: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
