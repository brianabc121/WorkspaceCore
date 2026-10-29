"""运行配置：只从环境变量读取。

连接串可能带密码，用 SecretStr 保存，避免出现在日志、repr 和错误信息里。
默认值只适用于本机开发（不含密码）；部署时由 Compose / Secret 注入环境变量覆盖。
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_url: SecretStr = SecretStr("postgresql+asyncpg://workspace@localhost:5432/workspace")
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    # /readyz 中每项依赖检查的超时时间
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=10)

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
