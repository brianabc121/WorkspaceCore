"""工作空间的图片存储设置（T-0223，网页“设置 → 图片存储”）。

主存储 / 副本选本机、Cloudflare R2 或阿里云 OSS。读取不返回密钥，只说是否已设置；
保存时密钥不填（null）表示保持不变，空字符串表示清除。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.schemas.operations import StorageCheck, StorageName

ReplicaName = Literal["none", "local", "r2", "oss"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class R2SettingsOut(BaseModel):
    account_id: str = Field(description="Cloudflare 账户 ID（R2 页面右侧的 Account ID）")
    bucket: str
    jurisdiction: str = Field(description="辖区（建桶时选了欧盟等辖区才填，例如 eu），没有就是空")
    access_key_id: str
    secret_set: bool = Field(description="Secret Access Key 是否已设置（不返回明文）")


class OssSettingsOut(BaseModel):
    region: str = Field(description="地域，例如 cn-hangzhou")
    bucket: str
    access_key_id: str
    secret_set: bool = Field(description="AccessKey Secret 是否已设置（不返回明文）")


class StorageSettingsOut(BaseModel):
    primary: StorageName
    replica: ReplicaName
    r2: R2SettingsOut
    oss: OssSettingsOut
    in_use: list[StorageName] = Field(
        description="还存着本工作空间图片或快照的存储；它们的连接信息不能清空，否则那些文件读不到"
    )
    version: int = Field(description="保存时带上；没保存过是 0")
    updated_at: datetime | None
    checks: dict[str, StorageCheck] | None = Field(
        default=None, description="保存时做的自检结果（只在保存的响应里返回）"
    )


class R2SettingsIn(_Strict):
    account_id: str = Field(default="", max_length=64)
    bucket: str = Field(default="", max_length=63)
    jurisdiction: str = Field(default="", max_length=16)
    access_key_id: str = Field(default="", max_length=128)
    secret_access_key: SecretStr | None = Field(
        default=None, description="不填（null）保持不变；空字符串清除"
    )


class OssSettingsIn(_Strict):
    region: str = Field(default="", max_length=40)
    bucket: str = Field(default="", max_length=63)
    access_key_id: str = Field(default="", max_length=128)
    access_key_secret: SecretStr | None = Field(
        default=None, description="不填（null）保持不变；空字符串清除"
    )


class StorageSettingsIn(_Strict):
    version: int = Field(ge=0, description="读取时的 version；没保存过是 0")
    primary: StorageName
    replica: ReplicaName = "none"
    r2: R2SettingsIn = Field(default_factory=R2SettingsIn)
    oss: OssSettingsIn = Field(default_factory=OssSettingsIn)
