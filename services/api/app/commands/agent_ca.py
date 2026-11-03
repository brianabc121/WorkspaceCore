"""内部 CA（V2 OC-2）：给 Caddy 的外网接入入口导出 CA 证书、签发入口证书。

    docker compose exec -T api python -m app.commands.agent_ca export [路径]
    docker compose exec -T api python -m app.commands.agent_ca issue-server --host <主机名>
    docker compose exec -T api python -m app.commands.agent_ca check-server --host <主机名> < x.crt

- 没有在用的 CA 时先生成一套（与第一次生成外网接入包时相同）；
- export 只写出证书（PEM），从不输出私钥；不给路径时写到标准输出。
  Caddy 的外网入口用它校验实例的客户端证书（`client_auth` 的 trust pool，OC-2 第 4 部分）；
- issue-server（T-0088）：签发外网入口的服务器证书，
  向标准输出写一个 JSON（cert、key、ca、not_after），由 deploy.sh 写进 AGENT_TLS_DIR。
  私钥只出现在这次输出里，不写日志、不存数据库；
- check-server（T-0088）：从标准输入读已有的入口证书，输出 ok 或 renew
  （快到期、主机名不对、不是这套 CA 签发的）。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.resources import create_engine
from app.domain.agent_ca import ensure_ca, issue_server_certificate, server_certificate_status
from app.domain.credentials import CredentialBox
from app.models import AgentCa


async def _load_ca() -> tuple[AgentCa, CredentialBox]:
    settings = get_settings()
    box = CredentialBox.from_settings(settings)
    engine = create_engine(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            ca = await ensure_ca(db, box)
            await db.commit()
            return ca, box
    finally:
        await engine.dispose()


async def export_pem() -> str:
    ca, _ = await _load_ca()
    return ca.cert_pem


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="内部 CA：导出 CA 证书、签发外网入口的服务器证书")
    sub = parser.add_subparsers(dest="action", required=True)
    export = sub.add_parser("export", help="写出 CA 证书（PEM）")
    export.add_argument("path", nargs="?", help="输出文件；不给时写到标准输出")
    issue = sub.add_parser("issue-server", help="签发外网入口的服务器证书（JSON 写到标准输出）")
    issue.add_argument("--host", required=True, help="入口主机名（证书的 SAN）")
    check = sub.add_parser("check-server", help="检查标准输入里的入口证书：ok 或 renew")
    check.add_argument("--host", required=True, help="入口主机名")
    args = parser.parse_args(argv)
    if args.action == "export":
        pem = asyncio.run(export_pem())
        if args.path:
            Path(args.path).write_text(pem, encoding="utf-8")
            print(f"已写出内部 CA 证书：{args.path}", file=sys.stderr)
        else:
            sys.stdout.write(pem)
        return 0
    ca, box = asyncio.run(_load_ca())
    if args.action == "issue-server":
        try:
            issued = issue_server_certificate(ca, box, args.host)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2
        json.dump(
            {
                "cert": issued.cert_pem,
                "key": issued.key_pem,
                "ca": issued.ca_pem,
                "not_after": issued.not_after.isoformat(),
            },
            sys.stdout,
        )
        return 0
    print(server_certificate_status(ca, sys.stdin.read(), args.host))
    return 0


if __name__ == "__main__":
    sys.exit(main())
