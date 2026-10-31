"""把用旧加密密钥保存的凭证重新加密为当前密钥（V2 OC-2）。

    docker compose exec -T api python -m app.commands.rotate_credentials [--dry-run]

前提：CREDENTIAL_ENCRYPTION_KEY 已换成新密钥，旧密钥在 CREDENTIAL_ENCRYPTION_KEYS_PREVIOUS 里。
可以重复运行。“仍是旧密钥”为 0 才可以从配置里删掉旧密钥。退出码：0 完成；1 有失败或还有旧密文。
只输出 key_id 与条数，不输出任何密钥或密文。
"""

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.resources import create_engine
from app.domain.credential_rotation import RotationReport, rotate
from app.domain.credentials import CredentialBox


async def run(dry_run: bool) -> RotationReport:
    settings = get_settings()
    box = CredentialBox.from_settings(settings)
    engine = create_engine(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            return await rotate(db, box, dry_run=dry_run)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把旧密钥加密的凭证重新加密为当前密钥")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写入")
    args = parser.parse_args(argv)
    report = asyncio.run(run(args.dry_run))
    verb = "可重新加密" if args.dry_run else "已重新加密"
    print(f"当前密钥：{report.current_key_id}")
    for name, field in report.fields.items():
        keys = "、".join(f"{k}×{n}" for k, n in sorted(field.by_key.items())) or "无"
        print(f"  {name}：{verb} {field.rotated}，失败 {field.failed}；现在的分布 {keys}")
    print(f"仍是旧密钥：{report.remaining_old}；失败：{report.failed}")
    return 1 if report.failed or (report.remaining_old and not args.dry_run) else 0


if __name__ == "__main__":
    sys.exit(main())
