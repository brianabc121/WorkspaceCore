"""模拟 OpenIM REST（V2 OC-3 的接口测试用）：只实现工作台用到的接口与语义。

语义来自对补丁镜像的实测（test_openim_live.py）：重复注册返回 1102；import_friend 是双向的；
is_friend 分别给出两个方向；签发用户令牌返回 expireTimeSeconds。
"""

import json
from typing import Any

import httpx

HOST = "openim.test"
BASE_URL = f"http://{HOST}:10002"
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

    def paths(self, path: str) -> list[dict[str, Any]]:
        return [body for p, body in self.calls if p == path]

    def handler(self, request: httpx.Request) -> httpx.Response:
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

    def befriended(self, a: str, b: str) -> bool:
        return (a, b) in self.friends and (b, a) in self.friends
