"""模拟 OpenIM REST（V2 OC-3 的接口测试用）：只实现工作台用到的接口与语义。

语义来自对补丁镜像的实测（test_openim_live.py）：重复注册返回 1102；import_friend 是双向的；
is_friend 分别给出两个方向；签发用户令牌返回 expireTimeSeconds。
"""

import base64
import hashlib
import json
import uuid
from typing import Any

import httpx

HOST = "openim.test"
BASE_URL = f"http://{HOST}:10002"
# 对象存储（Garage）：OpenIM 签出的上传与下载地址指向它
OBJECT_HOST = "objects.test"
OBJECT_URL = f"http://{OBJECT_HOST}:3900"
SECRET = "fake-openim-secret"  # noqa: S105 - 测试用的假 secret
TOKEN_SECONDS = 30 * 24 * 3600


class FakeOpenIM:
    def __init__(self) -> None:
        self.users: dict[str, str] = {}
        self.friends: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.logged_out: list[str] = []
        self.tokens_issued: list[str] = []
        # 设为 True 时除了换管理员令牌之外一律返回 HTTP 503
        self.down = False
        # 会话 ID → 按序号排列的 MsgData；会话 ID → 成员（能拉取、能在 newest_seq 里看到）
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.members: dict[str, set[str]] = {}
        self.online: set[str] = set()
        # 代发的故障注入：send_error 为 errCode（拒收）；send_timeout 时消息照常入库但应答超时
        self.send_error = 0
        self.send_timeout = False
        # 设为 True 时连接被拒（请求确定没有发出）
        self.refuse = False
        # 群 ID → {"name", "notification", "owner", "dismissed"}；成员记在 members["sg_<群>"]
        self.groups: dict[str, dict[str, Any]] = {}
        # 对象名 → 内容；上传中的分片按 uploadID 暂存
        self.objects: dict[str, bytes] = {}
        self.pending_parts: dict[str, dict[int, bytes]] = {}

    def add_message(
        self,
        conversation_id: str,
        send_id: str,
        content: dict[str, Any] | None = None,
        *,
        content_type: int = 101,
        members: set[str] | None = None,
    ) -> dict[str, Any]:
        """往会话里追加一条消息（序号自动递增），返回 MsgData。"""
        if members:
            self.members.setdefault(conversation_id, set()).update(members)
        body = conversation_id[3:]
        if conversation_id.startswith("si_") and len(body) == 71:
            # 单聊的两个用户就是成员
            self.members.setdefault(conversation_id, set()).update({body[:35], body[36:]})
        log = self.messages.setdefault(conversation_id, [])
        seq = len(log) + 1
        msg = {
            "sendID": send_id,
            "serverMsgID": uuid.uuid4().hex,
            "clientMsgID": uuid.uuid4().hex,
            "seq": seq,
            "contentType": content_type,
            "content": base64.b64encode(json.dumps(content or {}).encode()).decode(),
            "sendTime": 1700000000000 + seq * 1000,
            "sessionType": 3 if conversation_id.startswith("sg_") else 1,
        }
        log.append(msg)
        return msg

    def paths(self, path: str) -> list[dict[str, Any]]:
        return [body for p, body in self.calls if p == path]

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.refuse:
            raise httpx.ConnectError("simulated refusal")
        if request.url.host == OBJECT_HOST:
            return self._object(request)
        if request.url.host != HOST:
            return httpx.Response(404)
        path = request.url.path
        body: dict[str, Any] = json.loads(request.content or b"{}")
        self.calls.append((path, body))
        if path == "/auth/get_admin_token":
            if body.get("secret") != SECRET:
                return self._error(1002)
            return self._ok({"token": "admin-token", "expireTimeSeconds": 7776000})
        if self.down:
            return httpx.Response(503, text="unavailable")
        if request.headers.get("token") != "admin-token":
            return self._error(1501)
        handler = getattr(self, "_" + path.strip("/").replace("/", "_"), None)
        if handler is None:
            return self._ok({})
        result: httpx.Response = handler(body)
        return result

    @staticmethod
    def _ok(data: dict[str, Any]) -> httpx.Response:
        return httpx.Response(200, json={"errCode": 0, "errMsg": "", "data": data})

    @staticmethod
    def _error(code: int) -> httpx.Response:
        return httpx.Response(200, json={"errCode": code, "errMsg": "error", "errDlt": "detail"})

    def _user_user_register(self, body: dict[str, Any]) -> httpx.Response:
        users = body.get("users") or []
        if any(u["userID"] in self.users for u in users):
            return self._error(1102)
        for user in users:
            self.users[user["userID"]] = user.get("nickname", "")
        return self._ok({})

    def _user_account_check(self, body: dict[str, Any]) -> httpx.Response:
        return self._ok(
            {
                "results": [
                    {"userID": u, "accountStatus": 1 if u in self.users else 0}
                    for u in body.get("checkUserIDs") or []
                ]
            }
        )

    def _friend_is_friend(self, body: dict[str, Any]) -> httpx.Response:
        a, b = body["userID1"], body["userID2"]
        return self._ok(
            {"inUser1Friends": (a, b) in self.friends, "inUser2Friends": (b, a) in self.friends}
        )

    def _friend_import_friend(self, body: dict[str, Any]) -> httpx.Response:
        owner = body["ownerUserID"]
        for friend in body.get("friendUserIDs") or []:
            if owner not in self.users or friend not in self.users:
                return self._error(1101)
            self.friends |= {(owner, friend), (friend, owner)}
        return self._ok({})

    def _auth_get_user_token(self, body: dict[str, Any]) -> httpx.Response:
        user_id = body["userID"]
        if user_id not in self.users:
            return self._error(1101)
        self.tokens_issued.append(user_id)
        return self._ok(
            {"token": f"user-token-{len(self.tokens_issued)}", "expireTimeSeconds": TOKEN_SECONDS}
        )

    def _auth_force_logout(self, body: dict[str, Any]) -> httpx.Response:
        self.logged_out.append(body["userID"])
        return self._ok({})

    def _msg_newest_seq(self, body: dict[str, Any]) -> httpx.Response:
        user = body["userID"]
        return self._ok(
            {
                "maxSeqs": {
                    conv: len(log)
                    for conv, log in self.messages.items()
                    if user in self.members.get(conv, set())
                }
            }
        )

    def _msg_pull_msg_by_seq(self, body: dict[str, Any]) -> httpx.Response:
        user = body["userID"]
        result: dict[str, Any] = {}
        for item in body.get("seqRanges") or []:
            conv = item["conversationID"]
            if user not in self.members.get(conv, set()):
                result[conv] = {"Msgs": [], "isEnd": True}
                continue
            msgs = [
                m for m in self.messages.get(conv, []) if item["begin"] <= m["seq"] <= item["end"]
            ][: item["num"]]
            result[conv] = {"Msgs": msgs, "isEnd": True}
        return self._ok({"msgs": result})

    def _user_get_users_online_status(self, body: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errCode": 0,
                "errMsg": "",
                "data": [
                    {"userID": u, "status": 1 if u in self.online else 0}
                    for u in body.get("userIDs") or []
                ],
            },
        )

    def _msg_send_msg(self, body: dict[str, Any]) -> httpx.Response:
        if self.send_error:
            return self._error(self.send_error)
        sender, group = body["sendID"], body.get("groupID") or ""
        if group:
            conv = f"sg_{group}"
        else:
            first, second = sorted((sender, body["recvID"]))
            conv = f"si_{first}_{second}"
            self.members.setdefault(conv, set()).update({first, second})
        msg = self.add_message(
            conv, sender, body.get("content") or {}, content_type=int(body["contentType"])
        )
        msg["ex"] = body.get("ex") or ""
        if self.send_timeout:
            raise httpx.ReadTimeout("simulated timeout")
        return self._ok(
            {
                "serverMsgID": msg["serverMsgID"],
                "clientMsgID": msg["clientMsgID"],
                "sendTime": msg["sendTime"],
            }
        )

    def _group_create_group(self, body: dict[str, Any]) -> httpx.Response:
        info = body.get("groupInfo") or {}
        group_id = info.get("groupID") or uuid.uuid4().hex[:10]
        if group_id in self.groups:
            return self._error(1202)
        # 与真实实例一致：调用者 imAdmin 不入群
        members = {body["ownerUserID"], *body.get("memberUserIDs", [])}
        if any(m not in self.users for m in members):
            return self._error(1101)
        self.groups[group_id] = {
            "name": info.get("groupName"),
            "notification": info.get("notification", ""),
            "owner": body["ownerUserID"],
            "dismissed": False,
        }
        self.members[f"sg_{group_id}"] = members
        return self._ok({"groupInfo": {"groupID": group_id}})

    def _live(self, group_id: str) -> dict[str, Any] | None:
        group = self.groups.get(group_id)
        return group if group and not group["dismissed"] else None

    def _group_invite_user_to_group(self, body: dict[str, Any]) -> httpx.Response:
        if self._live(body["groupID"]) is None:
            return self._error(1204)
        self.members[f"sg_{body['groupID']}"].update(body["invitedUserIDs"])
        return self._ok({})

    def _group_kick_group(self, body: dict[str, Any]) -> httpx.Response:
        if self._live(body["groupID"]) is None:
            return self._error(1204)
        self.members[f"sg_{body['groupID']}"].difference_update(body["kickedUserIDs"])
        return self._ok({})

    def _group_set_group_info_ex(self, body: dict[str, Any]) -> httpx.Response:
        group = self._live(body["groupID"])
        if group is None:
            return self._error(1204)
        if "groupName" in body:
            group["name"] = body["groupName"]
        if "notification" in body:
            group["notification"] = body["notification"]
        return self._ok({})

    def _group_dismiss_group(self, body: dict[str, Any]) -> httpx.Response:
        group = self._live(body["groupID"])
        if group is None:
            return self._error(1204)
        group["dismissed"] = True
        return self._ok({})

    # ---------------------------------------------------------------- 对象存储

    def _object(self, request: httpx.Request) -> httpx.Response:
        name = request.url.path.lstrip("/")
        if request.method == "PUT":
            upload_id = request.url.params.get("uploadId", "")
            number = int(request.url.params.get("partNumber", "0"))
            body = request.content
            self.pending_parts.setdefault(upload_id, {})[number] = body
            etag = hashlib.md5(body, usedforsecurity=False).hexdigest()
            return httpx.Response(200, headers={"ETag": f'"{etag}"'})
        if request.method == "GET":
            data = self.objects.get(name)
            if data is None:
                return httpx.Response(404)
            return httpx.Response(200, content=data, headers={"Content-Type": "image/png"})
        return httpx.Response(405)

    def _object_part_size(self, body: dict[str, Any]) -> httpx.Response:
        return self._ok({"size": 5 * 1024 * 1024})

    def _object_initiate_multipart_upload(self, body: dict[str, Any]) -> httpx.Response:
        upload_id = uuid.uuid4().hex
        size, part_size = int(body["size"]), int(body["partSize"])
        count = max(1, -(-size // part_size))
        self.pending_parts[upload_id] = {}
        return self._ok(
            {
                "upload": {
                    "uploadID": upload_id,
                    "sign": {
                        "url": f"{OBJECT_URL}/{body['name']}",
                        "query": [{"key": "uploadId", "values": [upload_id]}],
                        "parts": [
                            {
                                "partNumber": i,
                                "query": [{"key": "partNumber", "values": [str(i)]}],
                            }
                            for i in range(1, count + 1)
                        ],
                    },
                }
            }
        )

    def _object_complete_multipart_upload(self, body: dict[str, Any]) -> httpx.Response:
        parts = self.pending_parts.pop(body["uploadID"], {})
        self.objects[body["name"]] = b"".join(parts[i] for i in sorted(parts))
        return self._ok({"url": f"{BASE_URL}/object/{body['name']}"})

    def _object_access_url(self, body: dict[str, Any]) -> httpx.Response:
        if body["name"] not in self.objects:
            return self._error(1004)
        return self._ok({"url": f"{OBJECT_URL}/{body['name']}"})

    def unfriend(self, a: str, b: str) -> None:
        self.friends -= {(a, b), (b, a)}

    def befriended(self, a: str, b: str) -> bool:
        return (a, b) in self.friends and (b, a) in self.friends
