"""每晚复盘定时任务的设置（T-0100）：AGENT_REFLECTION_CRON / AGENT_REFLECTION_TIMEZONE 启动时校验。

这两个值会写进接入包的 setup-reflection.sh（双引号里的命令参数）。
所以只接受 5 段 cron 的字符和真实存在的 IANA 时区名；带引号、`$`、反引号之类的值在启动时就拒绝。
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_defaults_are_nightly_in_shanghai() -> None:
    settings = Settings(app_env="test")
    assert settings.agent_reflection_cron == "0 22 * * *"
    assert settings.agent_reflection_timezone == "Asia/Shanghai"


@pytest.mark.parametrize("cron", ["30 21 * * 1-5", "*/30 * * * *", "0 9,21 * * *"])
def test_accepts_five_field_cron(cron: str) -> None:
    assert Settings(app_env="test", agent_reflection_cron=cron).agent_reflection_cron == cron


@pytest.mark.parametrize(
    "cron", ["0 22 * *", "0 22 * * * *", "@daily", '0 22 * * *"; rm -rf ~; "', "0 22 * * $(id)"]
)
def test_rejects_other_cron_values(cron: str) -> None:
    with pytest.raises(ValidationError, match="AGENT_REFLECTION_CRON"):
        Settings(app_env="test", agent_reflection_cron=cron)


@pytest.mark.parametrize("zone", ["Mars/Base", "Asia/Shanghai$(id)", '"; id; "', "../etc/passwd"])
def test_rejects_unknown_timezones(zone: str) -> None:
    with pytest.raises(ValidationError, match="AGENT_REFLECTION_TIMEZONE"):
        Settings(app_env="test", agent_reflection_timezone=zone)
