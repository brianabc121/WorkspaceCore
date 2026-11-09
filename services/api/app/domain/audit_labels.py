"""审计日志的展示补充（T-0197，契约 docs/v2/ai-admin-redesign/…-contract.md 第 5.1 节）。

- action_label：内置事件的中文名；未知事件返回 None，界面照原样显示 action。
  名称只描述“记了什么事件”，不推断结果：例如部署“已发起”不写成“成功”。
- actor_label：只在当前工作空间的成员里解析显示名；actor_id 为空是“系统”；
  已不是成员（被移出、账号已删）返回 None，界面退回显示 ID。
- resource_current_name：对象“现在”的名称，不是记录当时的快照。只查本地表、批量查、按调用者
  当前的读取权限（例如密钥库只有 owner 能读，admin 拿到 None）；对象已删除、ID 不合法或不属于
  当前工作空间都是 None，不影响整页。不发任何第三方请求。
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.policy import WorkspaceAccess, has_role
from app.models import (
    AgentInstance,
    AgentSecret,
    AgentServer,
    AuditLog,
    Integration,
    Project,
    Task,
    User,
    WorkspaceMember,
)
from app.schemas.workspaces import Role

SYSTEM_ACTOR_LABEL = "系统"

_LABELS: dict[str, str] = {
    # 首次安装、登录与账户
    "setup.completed": "完成首次安装",
    "auth.login_succeeded": "登录成功",
    "auth.login_failed": "登录失败",
    "auth.logout": "登出",
    "auth.password_changed": "修改密码",
    "auth.password_change_failed": "修改密码失败",
    "auth.session_revoked": "撤销会话",
    "user.created": "创建账号",
    "user.updated": "修改个人资料",
    # 工作空间、成员、模块与侧栏
    "workspace.updated": "修改工作空间设置",
    "workspace.member_added": "添加成员",
    "workspace.member_removed": "移除成员",
    "workspace.member_role_changed": "修改成员角色",
    "module.enabled": "启用模块",
    "module.disabled": "停用模块",
    "navigation.updated": "修改侧栏布局",
    # 审计本身
    "audit.export_generated": "导出审计日志",
    # 首页
    "dashboard.created": "创建首页看板",
    "dashboard.updated": "修改首页看板",
    "dashboard.reset": "重置首页看板",
    "widget.created": "添加首页组件",
    "widget.updated": "修改首页组件",
    "widget.deleted": "删除首页组件",
    # 集成
    "integration.created": "添加集成",
    "integration.updated": "修改集成",
    "integration.tested": "测试集成连接",
    "integration.sync_requested": "请求同步集成",
    # 项目、任务与 Issue
    "project.created": "创建项目",
    "project.updated": "修改项目",
    "project.deleted": "删除项目",
    "project.agent_added": "把智能体加入项目",
    "project.agent_removed": "把智能体移出项目",
    "project.im_group_requested": "申请建项目群",
    "task.created": "创建任务",
    "task.updated": "修改任务",
    "task.deleted": "删除任务",
    "task.claimed": "领取任务",
    "task.released": "放弃任务",
    "task.completed": "完成任务",
    "task.reopened": "打回任务",
    "task.blocked": "标记任务阻塞",
    "task.unblocked": "解除任务阻塞",
    "task.run.started": "开始执行任务",
    "task.run.dispatched": "把任务交给智能体执行",
    "task.run.stopped": "停止执行任务",
    "issues.issue.deleted": "删除 Issue",
    "issues.notices_enabled": "打开 Issue 群提示",
    "issues.notices_disabled": "关闭 Issue 群提示",
    # 日历、收藏与知识库
    "calendar_event.created": "创建日程",
    "calendar_event.updated": "修改日程",
    "calendar_event.deleted": "删除日程",
    "bookmark.created": "添加收藏",
    "bookmark.updated": "修改收藏",
    "bookmark.deleted": "删除收藏",
    "knowledge_source.created": "添加知识来源",
    "knowledge_source.updated": "修改知识来源",
    "knowledge_source.deleted": "删除知识来源",
    "knowledge_space.updated": "修改知识空间",
    "knowledge_space.agent_added": "给智能体开放知识空间",
    "knowledge_space.agent_removed": "收回智能体的知识空间权限",
    "knowledge_note.created": "创建笔记",
    "knowledge_note.updated": "修改笔记",
    "knowledge_note.deleted": "把笔记移到回收站",
    "knowledge_note.restored": "从回收站恢复笔记",
    "knowledge_note.reverted": "把笔记恢复到历史版本",
    "knowledge_note.purged": "彻底删除笔记",
    # 智能体实例、凭证与密钥库
    "agents.settings.updated": "修改智能体总开关",
    "agents.instance.created": "登记智能体实例",
    "agents.instance.updated": "修改智能体实例",
    "agents.instance.enabled": "启用智能体实例",
    "agents.instance.disabled": "停用智能体实例",
    "agents.instance.limits_updated": "修改实例资源上限",
    "agents.instance.mail_updated": "修改实例邮件权限",
    "agents.instance.runner_updated": "修改实例运行环境权限",
    "agents.instance.server_assigned": "指定实例所在服务器",
    "agents.instance.im_media_enabled": "打开实例附件权限",
    "agents.instance.im_media_disabled": "关闭实例附件权限",
    "agents.instance.project_admin_enabled": "打开实例项目管理权限",
    "agents.instance.project_admin_disabled": "关闭实例项目管理权限",
    "agents.instance.stop_others_enabled": "打开实例终止其他任务的权限",
    "agents.instance.stop_others_disabled": "关闭实例终止其他任务的权限",
    "agents.bundle.issued": "生成接入包并签发凭证",
    "agents.credential.revoked": "吊销实例凭证",
    "agents.grants.replaced": "修改实例的密钥授权",
    "agents.secret.created": "新增密钥",
    "agents.secret.updated": "修改密钥",
    "agents.secret.rotated": "轮换密钥",
    "agents.secret.deleted": "删除密钥",
    "agents.ops.started": "发起运维动作",
    "agents.ops.finished": "运维动作结束",
    "agents.run.stop_requested": "请求终止智能体任务",
    "agents.wakeup.created": "安排唤醒",
    "agents.wakeup.cancelled": "取消唤醒",
    # OpenClaw 服务器
    "agents.server.created": "添加 OpenClaw 服务器",
    "agents.server.renamed": "重命名 OpenClaw 服务器",
    "agents.server.removed": "移除 OpenClaw 服务器",
    "agents.server.address_set": "设置服务器地址",
    "agents.server.credentials_set": "保存服务器登录凭据",
    "agents.server.connection_tested": "测试服务器连接",
    "agents.server.host_key_confirmed": "确认服务器指纹",
    "agents.server.legacy_key_cleaned": "清理服务器上的旧公钥",
    "agents.server.legacy_key_cleanup_failed": "清理服务器旧公钥失败",
    # IM
    "im.group.created": "创建群聊",
    "im.group.dismissed": "解散群聊",
    "im.group.policy_updated": "修改群聊规则",
    "im.conversation.exported": "导出会话",
    # 邮件
    "mail.client_updated": "修改 Gmail 应用凭据",
    "mail.connected": "连接邮箱",
    "mail.disconnected": "断开邮箱",
    "mail.account_updated": "修改邮箱设置",
    "mail.account_agents_updated": "修改邮箱分配的智能体",
    "mail.sent": "发出邮件",
    "mail.send_unknown": "发信结果未知",
    # 新闻早报
    "news.settings_updated": "修改新闻早报设置",
    "news.exclusion_added": "添加新闻排除词",
    "news.test_push": "测试早报推送",
    # 容器服务器与运行环境
    "runner.host_created": "添加容器服务器",
    "runner.host_reissued": "重新签发容器服务器凭证",
    "runner.host_removed": "移除容器服务器",
    "runner.policy_updated": "修改运行环境策略",
    "runner.template_saved": "保存环境模板",
    "runner.template_rebuild": "重建环境模板",
    "runner.template_status": "修改环境模板状态",
    "runner.environment_created": "创建容器环境",
    "runner.ssh.key_authorized": "授权容器服务器 SSH 公钥",
    "runner.ssh.key_revoked": "撤销容器服务器 SSH 公钥",
    "runner.ssh.target_set": "设置容器服务器 SSH 地址",
    "runner.ssh.target_removed": "移除容器服务器 SSH 地址",
    "runner.ssh.host_key_confirmed": "确认容器服务器指纹",
}

# 远程任务：只有 started 表示“已发起”，结果另有 succeeded / failed / interrupted 事件
_JOB_STATES = {
    "started": "开始{}（已发起）",
    "succeeded": "{}成功",
    "failed": "{}失败",
    "interrupted": "{}中断",
}
_JOB_KINDS = {
    "agents.ssh": {"prepare": "初始化服务器", "install": "部署智能体", "uninstall": "卸载智能体"},
    "runner.ssh": {"install": "安装容器服务器", "uninstall": "卸载容器服务器"},
}
for _prefix, _kinds in _JOB_KINDS.items():
    for _kind, _what in _kinds.items():
        for _state, _template in _JOB_STATES.items():
            _LABELS[f"{_prefix}.{_kind}_{_state}"] = _template.format(_what)

_ENVIRONMENT_ACTIONS = {
    "start": "启动",
    "stop": "停止",
    "archive": "归档",
    "delete": "删除",
    "keep": "保留",
    "restore": "恢复",
    "pin": "固定",
    "unpin": "取消固定",
}
for _action, _verb in _ENVIRONMENT_ACTIONS.items():
    _LABELS[f"runner.environment_{_action}"] = f"{_verb}容器环境"

ACTION_LABELS: Mapping[str, str] = MappingProxyType(_LABELS)


def action_label(action: str) -> str | None:
    return ACTION_LABELS.get(action)


def actions_matching_label(text: str) -> list[str]:
    """中文名包含 text（不区分大小写）的内置事件；用于关键词搜索时把“轮换密钥”换成 action。"""
    needle = text.casefold()
    if not needle:
        return []
    return sorted(action for action, label in ACTION_LABELS.items() if needle in label.casefold())


# ---------------------------------------------------------------- 当前名称


@dataclass(frozen=True)
class _NameSource:
    minimum: Role  # 调用者在当前工作空间至少要有这个角色，才能读这种对象的名称
    model: Any
    column: Any


# 只解析本地表里、调用者现在能读到的对象类型。密钥库、OpenClaw 服务器的列表与详情只有 owner 能看。
_NAME_SOURCES: Mapping[str, _NameSource] = MappingProxyType(
    {
        "project": _NameSource(Role.viewer, Project, Project.title),
        "task": _NameSource(Role.viewer, Task, Task.title),
        "integration": _NameSource(Role.viewer, Integration, Integration.name),
        "agent_instance": _NameSource(Role.admin, AgentInstance, AgentInstance.name),
        "agent_server": _NameSource(Role.owner, AgentServer, AgentServer.name),
        "agent_secret": _NameSource(Role.owner, AgentSecret, AgentSecret.name),
    }
)
RESOLVED_RESOURCE_TYPES = frozenset({*_NAME_SOURCES, "user", "workspace"})


def _uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


@dataclass
class AuditLabels:
    """一批审计记录的显示名：成员显示名与对象当前名称（都只在当前权限内）。"""

    members: dict[uuid.UUID, str] = field(default_factory=dict)
    names: dict[tuple[str, uuid.UUID], str] = field(default_factory=dict)

    def actor(self, actor_id: uuid.UUID | None) -> str | None:
        if actor_id is None:
            return SYSTEM_ACTOR_LABEL
        return self.members.get(actor_id)

    def resource(self, resource_type: str | None, resource_id: str | None) -> str | None:
        key = _uuid(resource_id)
        if resource_type is None or key is None:
            return None
        return self.names.get((resource_type, key))


async def _member_names(
    db: AsyncSession, workspace_id: uuid.UUID, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    wanted = list(set(user_ids))
    if not wanted:
        return {}
    rows = await db.execute(
        select(User.id, User.display_name)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == workspace_id, User.id.in_(wanted))
    )
    return {user_id: name for user_id, name in rows}


async def resolve_labels(
    db: AsyncSession, access: WorkspaceAccess, entries: Sequence[AuditLog]
) -> AuditLabels:
    """每种对象一次查询；成员显示名一次查询（操作者与 user 类对象合在一起）。"""
    workspace_id = access.workspace.id
    wanted: dict[str, set[uuid.UUID]] = defaultdict(set)
    for entry in entries:
        key = _uuid(entry.resource_id)
        if entry.resource_type in RESOLVED_RESOURCE_TYPES and key is not None:
            wanted[entry.resource_type].add(key)

    actors = {entry.actor_id for entry in entries if entry.actor_id is not None}
    labels = AuditLabels(members=await _member_names(db, workspace_id, actors | wanted["user"]))
    for user_id in wanted.pop("user", set()):
        if user_id in labels.members:
            labels.names["user", user_id] = labels.members[user_id]
    if workspace_id in wanted.pop("workspace", set()):
        labels.names["workspace", workspace_id] = access.workspace.name

    for resource_type, ids in wanted.items():
        source = _NAME_SOURCES[resource_type]
        if not ids or not has_role(access.role, source.minimum):
            continue
        rows = await db.execute(
            select(source.model.id, source.column).where(
                source.model.workspace_id == workspace_id, source.model.id.in_(list(ids))
            )
        )
        for object_id, name in rows:
            labels.names[resource_type, object_id] = name
    return labels
