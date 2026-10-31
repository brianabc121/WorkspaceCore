"""OpenIM 回调（内部接口，不进 OpenAPI）。

约定见 docs/decisions/2026-10-31-v2-oc1-contract.md 第 10 节。载荷字段来自 OC-0 在
open-im-server v3.8.3-patch.18 上抓到的实际回调（核验 ADR 第 2、3 节）：
- 回调地址是 `<配置的 URL>/<callbackCommand>`；请求头只有 Operationid 与 Content-Type，
  **没有签名**；
- 消息回调里的 `seq` 总是 0，所以投影要用 pull_msg_by_seq 补齐序号与内容，回调只当作“有变化”的提示；
- 只信任回调里的 ID，内容以对账结果为准（方案第 7 节）。

模型都允许多余字段（OpenIM 升级会加字段），只取用到的字段。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 工作台处理的回调；其余命令一律放行并忽略
HANDLED_COMMANDS = (
    "callbackBeforeSendSingleMsgCommand",
    "callbackBeforeSendGroupMsgCommand",
    "callbackAfterSendSingleMsgCommand",
    "callbackAfterSendGroupMsgCommand",
    "callbackAfterUserOnlineCommand",
    "callbackAfterUserOfflineCommand",
    "callbackAfterDeleteFriendCommand",
    "callbackAfterImportFriendsCommand",
)
BEFORE_COMMANDS = HANDLED_COMMANDS[:2]
MAX_CALLBACK_BYTES = 256 * 1024


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class MsgCallback(_Lenient):
    """before / after SendSingleMsg、SendGroupMsg 的共同字段。"""

    callback_command: str = Field(alias="callbackCommand")
    send_id: str = Field(alias="sendID")
    recv_id: str = Field(default="", alias="recvID", description="单聊的接收人；群聊为空")
    group_id: str = Field(default="", alias="groupID", description="群聊的群 ID；单聊为空")
    server_msg_id: str = Field(alias="serverMsgID")
    client_msg_id: str = Field(alias="clientMsgID")
    session_type: int = Field(alias="sessionType", description="1 单聊；3 群聊")
    content_type: int = Field(
        alias="contentType", description="101 文字、102 图片、1201 加好友通知、1501 建群通知等"
    )
    sender_platform_id: int = Field(default=0, alias="senderPlatformID")
    at_user_list: list[str] | None = Field(default=None, alias="atUserList")
    send_time: int = Field(default=0, alias="sendTime", description="毫秒时间戳")


class UserStatusCallback(_Lenient):
    """AfterUserOnline / AfterUserOffline：每个连接一次。"""

    callback_command: str = Field(alias="callbackCommand")
    user_id: str = Field(alias="userID")
    platform_id: int = Field(alias="platformID")
    seq: int = Field(default=0, description="OpenIM 给的毫秒时间戳，用于去重与排序")


class DeleteFriendCallback(_Lenient):
    callback_command: str = Field(alias="callbackCommand")
    owner_user_id: str = Field(alias="ownerUserID")
    friend_user_id: str = Field(alias="friendUserID")


class ImportFriendsCallback(_Lenient):
    callback_command: str = Field(alias="callbackCommand")
    owner_user_id: str = Field(alias="ownerUserID")
    friend_user_ids: list[str] = Field(default_factory=list, alias="friendUserIDs")


# 拒收时的 errCode（方案第 4.5 节；OC-0 实测 errCode 5001 + nextCode 1 能拒收，插件不重试）
RejectCode = Literal[5001, 5002, 5003, 5004, 5005, 5006, 5009]
REJECT_CODES: dict[int, str] = {
    5001: "RELAY_PAUSED",  # 管理员不发言时实例连续发言达到上限
    5002: "RELAY_RATE_LIMITED",  # 单个实例在这个群每分钟的条数达到上限
    5003: "AGENT_DM_FORBIDDEN",  # 实例之间单聊
    5004: "SENDER_DISABLED",  # 发送的实例已停用，或总开关已关闭
    5005: "NOT_ALLOWED",  # 跨工作空间、未知账号等其他不允许的情况
    5006: "RELAY_DISABLED",  # 这个群关闭了接力，实例的消息 @ 了其他实例
    5009: "POLICY_UNAVAILABLE",  # 工作台无法判断（Redis / 数据库不可用）：拒收，不放行
}


class CallbackResponse(BaseModel):
    """OpenIM 回调的应答格式。

    放行：全部为 0；拒收（只对 before 回调有效）：errCode 非 0、nextCode 为 1。
    """

    action_code: int = Field(default=0, serialization_alias="actionCode")
    err_code: int = Field(default=0, serialization_alias="errCode")
    err_msg: str = Field(default="", serialization_alias="errMsg")
    err_dlt: str = Field(default="", serialization_alias="errDlt")
    next_code: int = Field(default=0, serialization_alias="nextCode")

    @classmethod
    def allow(cls) -> "CallbackResponse":
        return cls()

    @classmethod
    def reject(cls, code: RejectCode) -> "CallbackResponse":
        return cls(err_code=code, err_msg=REJECT_CODES[code], next_code=1)
