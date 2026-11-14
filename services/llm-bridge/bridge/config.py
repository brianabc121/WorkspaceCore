"""部署级配置（环境变量，前缀 BRIDGE_）。需要管理员调整的设置在数据库里，经管理接口改。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BRIDGE_", extra="ignore")

    data_dir: Path = Path("/data")
    # 工作台（或部署者）持有的管理令牌：管理接口用它；带着它调用 /mcp 时可以用请求头代表某个调用方
    admin_token: str = Field(min_length=32)
    codex_bin: str = "codex"
    claude_bin: str = "claude"
    codebuddy_bin: str = "codebuddy"
    git_bin: str = "git"
    # 只给测试用：打开后多一个不访问任何外部服务的平台 fake
    enable_fake: bool = False
    # 等待驱动进程启动、普通请求的超时（秒）
    # “同一个平台两次开始之间至少隔几秒”在管理员没改过时的值；测试环境设成 0
    default_min_start_interval: int = Field(default=2, ge=0, le=60)
    rpc_timeout: float = 30.0
    clone_timeout: float = 300.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bridge.sqlite3"

    @property
    def home_dir(self) -> Path:
        return self.data_dir / "home"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"


@lru_cache
def get_settings() -> Settings:
    return Settings()
