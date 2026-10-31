"""内部 CA（V2 OC-2）：给 Caddy 的外网接入入口导出 CA 证书。

    docker compose exec -T api python -m app.commands.agent_ca export [路径]

- 没有在用的 CA 时先生成一套（与第一次生成外网接入包时相同）；
- 只写出证书（PEM），从不输出私钥；不给路径时写到标准输出。
Caddy 的外网入口用它校验实例的客户端证书（`client_auth` 的 trust pool，OC-2 第 4 部分）。
"""

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.resources import create_engine
from app.domain.agent_ca import ensure_ca
from app.domain.credentials import CredentialBox


async def export_pem() -> str:
    settings = get_settings()
    box = CredentialBox.from_settings(settings)
    engine = create_engine(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            ca = await ensure_ca(db, box)
            await db.commit()
            return ca.cert_pem
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出内部 CA 证书（不含私钥）")
    sub = parser.add_subparsers(dest="action", required=True)
    export = sub.add_parser("export", help="写出 CA 证书（PEM）")
    export.add_argument("path", nargs="?", help="输出文件；不给时写到标准输出")
    args = parser.parse_args(argv)
    pem = asyncio.run(export_pem())
    if args.path:
        Path(args.path).write_text(pem, encoding="utf-8")
        print(f"已写出内部 CA 证书：{args.path}", file=sys.stderr)
    else:
        sys.stdout.write(pem)
    return 0


if __name__ == "__main__":
    sys.exit(main())
