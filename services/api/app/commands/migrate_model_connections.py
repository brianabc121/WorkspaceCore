"""把旧密钥库按工作空间导入为中转站管理的连接映射（T-0203，MC-3）。

    docker compose exec -T api python -m app.commands.migrate_model_connections \\
        --workspace-id <工作空间 UUID> (--dry-run | --apply [--batch-size 200] | --report)

必须指定工作空间和一种模式，没有“全部工作空间”的默认值；全库迁移由发布流程逐个工作空间调用。
- --dry-run：只读盘点（将新建 / 已映射 / 待完善 / 待归类 / 不可解密的数量）；可以在内存里解密检查
  能否读取，不写任何记录；
- --apply：按批（默认每批 200 条）各自一个事务导入，可以重复运行；一批出错只回滚这一批，
  重跑时接着导入没完成的；并行运行不会重复；
- --report：只读对账（映射、旧授权与连接授权、绑定、取用记录的数量）。

不复制密文、不换 ID、不建模型或绑定、不访问外部服务。输出一份 JSON（计数、每批结果与错误类别），
不含 Key、密文、名称或异常原文。
退出码：0 完成；1 有失败（重跑继续）；2 参数、工作空间或加密配置有问题。
步骤见 docs/v2/model-connections/docs/rollout/2026-11-09-model-connections-migration.md。
"""

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.resources import create_engine
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.model_connection_migration import (
    DEFAULT_BATCH_SIZE,
    MAX_BATCH_SIZE,
    Mode,
    WorkspaceNotFound,
    migrate_workspace,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNUSABLE = 2


def _batch_size(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("必须是整数") from None
    if not 1 <= value <= MAX_BATCH_SIZE:
        raise argparse.ArgumentTypeError(f"必须在 1 到 {MAX_BATCH_SIZE} 之间")
    return value


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.commands.migrate_model_connections",
        description="把旧密钥库按工作空间导入为中转站管理的连接映射（不复制密文、不换 ID）",
    )
    p.add_argument("--workspace-id", required=True, type=uuid.UUID, help="要处理的工作空间（必填）")
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true", help="只读盘点，不写任何记录")
    modes.add_argument("--apply", action="store_true", help="按批导入（可以重复运行）")
    modes.add_argument("--report", action="store_true", help="只读对账")
    p.add_argument(
        "--batch-size",
        type=_batch_size,
        default=DEFAULT_BATCH_SIZE,
        help=f"每批的条数（每批一个事务），默认 {DEFAULT_BATCH_SIZE}",
    )
    return p


def _mode(args: argparse.Namespace) -> Mode:
    if args.apply:
        return "apply"
    if args.report:
        return "report"
    return "dry_run"


def _error(mode: Mode, workspace_id: uuid.UUID, category: str) -> dict[str, Any]:
    return {"mode": mode, "workspace_id": str(workspace_id), "error": category}


async def run(
    workspace_id: uuid.UUID,
    mode: Mode,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    settings: Settings | None = None,
) -> tuple[dict[str, Any], int]:
    """执行一种模式，返回 (JSON 输出, 退出码)。只输出计数与错误类别。"""
    settings = settings or get_settings()
    try:
        box = CredentialBox.from_settings(settings)
    except CredentialError:
        return _error(mode, workspace_id, "encryption_unavailable"), EXIT_UNUSABLE
    engine = create_engine(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            report = await migrate_workspace(
                db, settings, box, workspace_id, mode=mode, batch_size=batch_size
            )
    except WorkspaceNotFound:
        return _error(mode, workspace_id, "workspace_not_found"), EXIT_UNUSABLE
    except Exception:
        # 不输出异常原文（可能带连接串或数据），只给类别
        return _error(mode, workspace_id, "database_unavailable"), EXIT_UNUSABLE
    finally:
        await engine.dispose()
    return report.as_dict(), EXIT_FAILED if report.failed else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    output, code = asyncio.run(run(args.workspace_id, _mode(args), batch_size=args.batch_size))
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
