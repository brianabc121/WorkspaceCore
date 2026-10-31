"""OpenIM 越权回归测试（REST 部分；WebSocket 部分见 ws-pull-other-user.mjs）。

ADR：docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md 第 4 节。
在 OpenIM 上建三个测试用户（admin / a / b），让 admin 给 b 发一条带暗号的私信，
再用 a 的令牌尝试读取或修改 admin 与 b 的数据。

  python3 -I rest-authz.py --api http://127.0.0.1:10002 --secret "$OPENIM_SECRET"

退出码：0 表示所有越权尝试都被挡住（打过补丁）；1 表示至少一项越权成功（官方 v3.8.3-patch.18 预期如此）；
2 表示 WebSocket 部分无法判断（连接失败或超时）。
只用标准库；只在测试环境运行（会注册测试用户）。
"""

import argparse
import json
import sys
import time
import urllib.request
import uuid


def post(api: str, path: str, body: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json", "operationID": uuid.uuid4().hex}
    if token:
        headers["token"] = token
    req = urllib.request.Request(api + path, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--secret", required=True)
    ap.add_argument("--ws", help="消息网关地址，默认与 --api 同主机的 10001 端口")
    ap.add_argument("--prefix", default="probe" + uuid.uuid4().hex[:6])
    args = ap.parse_args()
    api, p = args.api.rstrip("/"), args.prefix
    admin, a, b = f"{p}_admin", f"{p}_a", f"{p}_b"
    marker = "PROBE-SECRET-" + uuid.uuid4().hex[:8]

    tok = post(api, "/auth/get_admin_token", {"secret": args.secret, "userID": "imAdmin"})["data"]["token"]
    post(api, "/user/user_register", {"users": [{"userID": u, "nickname": u} for u in (admin, a, b)]}, tok)
    post(api, "/friend/import_friend", {"ownerUserID": admin, "friendUserIDs": [a, b]}, tok)
    post(api, "/msg/send_msg", {"sendID": admin, "recvID": b, "senderPlatformID": 5, "content": {"content": marker},
                                "contentType": 101, "sessionType": 1}, tok)
    time.sleep(2)
    a_tok = post(api, "/auth/get_user_token", {"userID": a, "platformID": 3}, tok)["data"]["token"]
    conv = "si_" + "_".join(sorted([admin, b]))

    def leaked(resp: dict) -> bool:
        return marker in json.dumps(resp, ensure_ascii=False)

    def pulled_any(resp: dict) -> bool:
        # 拉回别人会话里的任何一条消息都算越权（不只看暗号：序号区间与内容加工方式可能变）
        msgs = ((resp.get("data") or {}).get("msgs") or {}).get(conv) or {}
        return bool(msgs.get("Msgs")) or leaked(resp)

    checks = [
        ("search_msg 读取 admin↔b", lambda: leaked(post(api, "/msg/search_msg", {
            "sendID": admin, "recvID": b, "contentType": 101, "sessionType": 1,
            "pagination": {"pageNumber": 1, "showNumber": 10}}, a_tok))),
        ("pull_msg_by_seq 冒充 admin", lambda: pulled_any(post(api, "/msg/pull_msg_by_seq", {
            "userID": admin, "seqRanges": [{"conversationID": conv, "begin": 0, "end": 100, "num": 100}], "order": 0}, a_tok))),
        ("get_conversations 读取 admin 的会话", lambda: post(api, "/conversation/get_conversations", {
            "ownerUserID": admin, "conversationIDs": [conv]}, a_tok).get("errCode") == 0),
        ("set_conversations 修改 admin 的会话", lambda: post(api, "/conversation/set_conversations", {
            "userIDs": [admin], "conversation": {"conversationID": conv, "conversationType": 1, "userID": b, "isPinned": True}},
            a_tok).get("errCode") == 0),
    ]

    def ws_probe() -> bool:
        import pathlib, subprocess
        script = pathlib.Path(__file__).with_name("ws-pull-other-user.mjs")
        ws = args.api.replace("http", "ws", 1).rsplit(":", 1)[0] + ":10001" if not args.ws else args.ws
        out = subprocess.run(["node", str(script), a_tok, a, "3", admin, conv, marker, ws],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 2:
            raise RuntimeError(out.stdout.strip() or out.stderr.strip())
        return out.returncode == 1

    checks.append(("WebSocket 1002 冒充 admin 拉取 admin↔b", ws_probe))
    vulnerable = 0
    inconclusive = 0
    for name, fn in checks:
        try:
            hit = fn()
        except RuntimeError as exc:  # WebSocket 连不上或超时：无法判断，不能算挡住
            inconclusive += 1
            print(f"⚠️  无法判断  {name}（{exc}）")
            continue
        except Exception as exc:  # REST 接口直接拒绝（HTTP 错误）算挡住
            hit = False
            name += f"（请求被拒：{exc.__class__.__name__}）"
        vulnerable += hit
        print(("❌ 越权成功  " if hit else "✅ 已挡住    ") + name)
    if vulnerable:
        return 1
    return 2 if inconclusive else 0


if __name__ == "__main__":
    sys.exit(main())
