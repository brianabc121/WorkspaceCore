"""connect 命令的入口（T-0076）：退出码与错误输出，不需要数据库。"""

import argparse
from typing import NoReturn

import pytest

from app.commands import connect


@pytest.mark.parametrize(
    ("error", "code"),
    [(connect.Usage("还没有完成首次安装"), 2), (connect.Failed("测试连接没有通过"), 1)],
)
def test_main_maps_errors_to_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    code: int,
) -> None:
    async def fail(args: argparse.Namespace) -> NoReturn:
        raise error

    monkeypatch.setattr(connect, "run", fail)
    assert connect.main(["status"]) == code
    assert capsys.readouterr().err.startswith("失败  ")


def test_secrets_are_never_command_line_arguments() -> None:
    """密码与 Token 只从标准输入读：解析器里不能有接收它们的参数。"""
    parser = connect.parser()
    options = {
        option
        for action in parser._subparsers._group_actions[0].choices.values()  # type: ignore[union-attr]
        for sub in action._actions
        for option in sub.option_strings
    }
    assert not {o for o in options if "password" in o or "token" in o or "secret" in o}
