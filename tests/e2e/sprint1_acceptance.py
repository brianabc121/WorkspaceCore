#!/usr/bin/env python3
"""Sprint 1 全栈验收（T-0013）：经 Caddy 对外入口验证规范第 13 节的 DoD 与验收场景。

覆盖 A01（空库首次安装）、A02（未登录访问私有接口 401 且不泄露内容）、A18（登出后旧会话失效）、
跨工作空间访问（非成员 404、角色不够 403）、会话撤销、CSRF 与来源校验、迁移在最新版本。

前提：Compose 全栈已启动并迁移（make up && make migrate），数据库里还没有任何用户。
用法：python3 tests/e2e/sprint1_acceptance.py [--base-url http://127.0.0.1:8080]
  - 安装凭据取环境变量 SETUP_TOKEN，未设置时读取仓库根目录的 .env（make env 生成）。
  - 请求的 Origin 取 --base-url 的来源，它必须是 PUBLIC_BASE_URL 或在 CORS_ALLOWED_ORIGINS 里。
  - V1 只能通过首次安装创建用户，第二个用户由 `docker compose exec api` 用应用自身的代码写入。
只依赖 Python 标准库，共用工具在同目录的 e2e_support.py；CI（ci-infra.yml）与本机共用。
"""

import argparse
import sys
import uuid

from e2e_support import (
    Client,
    Report,
    compose_exec,
    default_base_url,
    parse_base_url,
    setup_token,
)

OWNER = {
    "email": "owner@e2e.example.com",
    "password": "e2e owner passphrase 1",
    "display_name": "E2E Owner",
    "workspace_name": "验收空间",
}
BOB = {"email": "bob@e2e.example.com", "password": "e2e bob passphrase 12"}

# 在 api 容器里用应用自身的代码写入第二个用户（及其工作空间）或成员关系
SEED_SCRIPT = r"""
import asyncio, os, sys, uuid
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.core.config import get_settings
from app.core.resources import create_engine
from app.domain.security import hash_password
from app.models import WorkspaceMember
from app.repositories import users, workspaces

async def main():
    engine = create_engine(get_settings())
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            if sys.argv[1] == "user":
                user = await users.create(
                    db,
                    email=os.environ["E2E_EMAIL"],
                    password_hash=hash_password(os.environ["E2E_PASSWORD"]),
                    display_name="E2E Bob",
                )
                workspace = await workspaces.create_with_owner(
                    db, name="Bob 的空间", owner_id=user.id
                )
                print(user.id, workspace.id)
            else:
                workspace_id, user_id, role = sys.argv[2:5]
                db.add(WorkspaceMember(
                    workspace_id=uuid.UUID(workspace_id), user_id=uuid.UUID(user_id), role=role
                ))
            await db.commit()
    finally:
        await engine.dispose()

asyncio.run(main())
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default=default_base_url())
    base = parse_base_url(parser.parse_args().base_url)
    report = Report()
    print(f"Sprint 1 全栈验收：{base}")

    # ---------------------------------------------------------------- 迁移
    current = compose_exec("api", "alembic", "current")
    report.check("迁移在最新版本（alembic current 为 head）", "(head)" in current, current.strip())

    # ---------------------------------------------------------------- A01 首次安装
    owner = Client(base)
    status = owner.request("GET", "/api/v1/setup/status")
    if not report.check(
        "A01 空库时需要首次安装", status.status == 200 and status.json() == {"setup_required": True}
    ):
        print("数据库里已经有用户：本脚本需要空库（全新的 Compose 数据卷）。")
        return 1
    wrong = owner.request("POST", "/api/v1/setup", {**OWNER, "setup_token": "wrong-token"})
    report.check("A01 安装凭据错误被拒绝", wrong.status == 403, f"{wrong.status} {wrong.code}")
    created = owner.request("POST", "/api/v1/setup", {**OWNER, "setup_token": setup_token()})
    report.check("A01 首次安装成功", created.status == 201, str(created.status))
    me = created.json() or {}
    owner_workspace = (me.get("workspaces") or [{}])[0].get("id")
    report.check(
        "A01 创建 owner 与默认工作空间",
        [(w["name"], w["role"]) for w in me.get("workspaces", [])]
        == [(OWNER["workspace_name"], "owner")],
    )
    cookies = created.headers.get_all("Set-Cookie") or []
    session_cookie = next((c for c in cookies if c.startswith("ws_session=")), "")
    report.check(
        "A01 会话 Cookie 为 HttpOnly + SameSite=Lax",
        "HttpOnly" in session_cookie and "samesite=lax" in session_cookie.lower(),
    )
    again = owner.request("POST", "/api/v1/setup", {**OWNER, "setup_token": setup_token()})
    report.check("A01 首次安装只能成功一次", again.status == 409, f"{again.status} {again.code}")

    # ---------------------------------------------------------------- A02 未登录
    anonymous = Client(base)
    private = [
        ("GET", "/api/v1/me", {}),
        ("GET", "/api/v1/workspaces", {}),
        ("GET", f"/api/v1/workspaces/{owner_workspace}", {}),
        ("GET", "/api/v1/auth/sessions", {}),
        ("GET", "/api/v1/audit-logs", {"X-Workspace-Id": str(owner_workspace)}),
    ]
    for method, path, headers in private:
        response = anonymous.request(method, path, headers=headers)
        leaked = OWNER["email"] in response.text or OWNER["workspace_name"] in response.text
        report.check(
            f"A02 未登录 {method} {path.replace(str(owner_workspace), '{id}')} → 401",
            response.status == 401 and response.code == "UNAUTHORIZED" and not leaked,
            f"{response.status} {response.code}",
        )
    forged = anonymous.request("GET", "/api/v1/me", headers={"Cookie": "ws_session=forged"})
    report.check("A02 伪造会话 Cookie → 401", forged.status == 401, str(forged.status))

    # ---------------------------------------------------------------- CSRF 与来源
    no_csrf = owner.request("PATCH", "/api/v1/me", {"display_name": "x"}, csrf=False)
    report.check("缺少 X-CSRF-Token 的写请求被拒绝", no_csrf.status == 403, str(no_csrf.code))
    evil = anonymous.request(
        "POST",
        "/api/v1/auth/login",
        {"email": OWNER["email"], "password": OWNER["password"]},
        headers={"Origin": "https://evil.example"},
    )
    report.check("不可信来源的登录被拒绝", evil.status == 403, str(evil.code))

    # ---------------------------------------------------------------- 跨工作空间
    seeded = compose_exec(
        "api",
        "python",
        "-c",
        SEED_SCRIPT,
        "user",
        env={"E2E_EMAIL": BOB["email"], "E2E_PASSWORD": BOB["password"]},
    )
    bob_id, bob_workspace = seeded.split()[-2:]
    bob = Client(base)
    login = bob.request("POST", "/api/v1/auth/login", BOB)
    report.check("第二个用户（bob）登录", login.status == 200, str(login.status))
    listed = bob.request("GET", "/api/v1/workspaces").json() or {"items": []}
    report.check(
        "bob 只能列出自己的工作空间", [w["id"] for w in listed["items"]] == [bob_workspace]
    )
    attempts = {
        "bob 读取 owner 的工作空间": bob.request("GET", f"/api/v1/workspaces/{owner_workspace}"),
        "bob 修改 owner 的工作空间": bob.request(
            "PATCH", f"/api/v1/workspaces/{owner_workspace}", {"name": "改掉"}
        ),
        "bob 读取 owner 的审计日志": bob.request(
            "GET", "/api/v1/audit-logs", headers={"X-Workspace-Id": owner_workspace}
        ),
        "owner 读取 bob 的工作空间": owner.request("GET", f"/api/v1/workspaces/{bob_workspace}"),
        "读取不存在的工作空间": bob.request("GET", f"/api/v1/workspaces/{uuid.uuid4()}"),
    }
    for name, response in attempts.items():
        report.check(
            f"跨工作空间：{name} → 404",
            response.status == 404 and response.code == "NOT_FOUND",
            f"{response.status} {response.code}",
        )
    unchanged = owner.request("GET", f"/api/v1/workspaces/{owner_workspace}").json() or {}
    report.check("owner 的工作空间名称未被改动", unchanged.get("name") == OWNER["workspace_name"])

    compose_exec("api", "python", "-c", SEED_SCRIPT, "member", owner_workspace, bob_id, "viewer")
    viewer = bob.request("GET", f"/api/v1/workspaces/{owner_workspace}")
    report.check(
        "角色：viewer 可以查看工作空间",
        viewer.status == 200 and (viewer.json() or {}).get("role") == "viewer",
        str(viewer.status),
    )
    edit = bob.request("PATCH", f"/api/v1/workspaces/{owner_workspace}", {"name": "改掉"})
    report.check("角色：viewer 不能修改工作空间 → 403", edit.status == 403, str(edit.code))
    audit = bob.request("GET", "/api/v1/audit-logs", headers={"X-Workspace-Id": owner_workspace})
    report.check("角色：viewer 不能查看审计日志 → 403", audit.status == 403, str(audit.code))

    # ---------------------------------------------------------------- 会话撤销与 A18
    phone = Client(base)
    phone.request(
        "POST", "/api/v1/auth/login", {"email": OWNER["email"], "password": OWNER["password"]}
    )
    sessions = (owner.request("GET", "/api/v1/auth/sessions").json() or {}).get("items", [])
    report.check("owner 的会话列表包含两台设备", len(sessions) == 2, str(len(sessions)))
    other = next((s["id"] for s in sessions if not s["current"]), None)
    revoked = owner.request("DELETE", f"/api/v1/auth/sessions/{other}")
    report.check("撤销另一台设备的会话", revoked.status == 204, str(revoked.status))
    phone_me = phone.request("GET", "/api/v1/me")
    report.check("被撤销的会话不能再访问 → 401", phone_me.status == 401, str(phone_me.status))

    old_token = owner.cookie("ws_session")
    logout = owner.request("POST", "/api/v1/auth/logout")
    report.check("A18 登出", logout.status == 204, str(logout.status))
    replay = Client(base).request(
        "GET", "/api/v1/me", headers={"Cookie": f"ws_session={old_token}"}
    )
    report.check(
        "A18 登出后旧会话不能再访问私有接口 → 401", replay.status == 401, str(replay.status)
    )

    owner_again = Client(base)
    owner_again.request(
        "POST", "/api/v1/auth/login", {"email": OWNER["email"], "password": OWNER["password"]}
    )
    logs = owner_again.request(
        "GET", "/api/v1/audit-logs?limit=100", headers={"X-Workspace-Id": owner_workspace}
    )
    actions = {entry["action"] for entry in (logs.json() or {}).get("items", [])}
    expected = {"setup.completed", "auth.login_succeeded", "auth.session_revoked", "auth.logout"}
    report.check(
        "审计日志记录了安装、登录、撤销会话与登出",
        logs.status == 200 and expected <= actions,
        ", ".join(sorted(expected - actions)) or "",
    )

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
