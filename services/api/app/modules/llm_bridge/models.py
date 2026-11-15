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
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
