"""新闻早报的接口模型（T-0153，docs/v2/NEWS_PLAN.md 第 4~9 节）。

- 设置：只有 owner 能改，admin 及以上能看；密钥只写不读（只回“已设置”）。
- 早报、热榜、RSS、搜索、收藏：成员与访客都能看；收藏 / 不感兴趣要 member。
- 条目里的标题、链接、来源名都来自第三方（NewsNow、RSS、大模型），页面按纯文字显示。
"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

PlatformId = Literal[
    "toutiao",
    "baidu",
    "wallstreetcn-hot",
    "thepaper",
    "bilibili-hot-search",
    "cls-hot",
    "ifeng",
    "tieba",
    "weibo",
    "douyin",
    "zhihu",
]
ChannelId = Literal[
    "feishu", "dingtalk", "wework", "telegram", "email", "ntfy", "bark", "slack", "webhook"
]
BriefingMode = Literal["trendradar", "agent"]
FilterMethod = Literal["keyword", "ai"]
BriefingSource = Literal["trendradar", "agent"]
BriefingStatus = Literal["receiving", "complete", "pending", "failed"]
FeedbackKind = Literal["favorite", "dismiss"]
SourceType = Literal["hotlist", "rss"]


def _one_line(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("不能为空")
    return value


def _no_brackets(value: str) -> str:
    if "[" in value or "]" in value:
        raise ValueError("不能包含方括号")
    return value


Word = Annotated[str, StringConstraints(min_length=1, max_length=60), AfterValidator(_one_line)]
TopicName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=30),
    AfterValidator(_one_line),
    AfterValidator(_no_brackets),
]
PushTime = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):(00|30)$")]
UrlText = Annotated[str, StringConstraints(max_length=500)]
FeedId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")]
# LiteLLM 的 provider/model，例如 deepseek/deepseek-chat、openai/gpt-4o-mini、ollama/qwen2.5
AiModel = Annotated[
    str,
    StringConstraints(max_length=100, pattern=r"^$|^[A-Za-z0-9_.-]+/[A-Za-z0-9_.:@/-]+$"),
]
SecretText = Annotated[str, StringConstraints(max_length=1000)]


class NewsTopicIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: TopicName
    keywords: list[Word] = Field(
        default_factory=list,
        max_length=30,
        description="任意一个出现就算这个主题；`/正则/` 写法按正则匹配",
    )
    required: list[Word] = Field(default_factory=list, max_length=5, description="必须同时出现")
    max_items: int = Field(default=10, ge=0, le=50, description="每天最多几条；0 不限")
    description: Annotated[str, StringConstraints(max_length=200)] = Field(
        default="", description="给 AI 筛选看的一句话兴趣说明"
    )
    enabled: bool = True

    @model_validator(mode="after")
    def _has_words(self) -> "NewsTopicIn":
        if not self.keywords and not self.required:
            raise ValueError("至少写一个关键词")
        return self


class NewsFeedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: FeedId
    name: Annotated[str, StringConstraints(min_length=1, max_length=60), AfterValidator(_one_line)]
    url: UrlText
    enabled: bool = True
    max_age_days: int = Field(default=0, ge=0, le=30, description="只看最近几天的；0 用默认 3 天")


class NewsChannelIn(BaseModel):
    """一个推送渠道。明文设置只对相应渠道有意义；
    密钥放在 secrets 里（不给的字段不改，空字符串清除）。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    msg_type: Literal["markdown", "text"] = Field(default="markdown", description="企业微信")
    chat_id: Annotated[str, StringConstraints(max_length=64)] = Field(
        default="", description="Telegram"
    )
    from_addr: Annotated[str, StringConstraints(max_length=200)] = Field(
        default="", description="邮件：发件地址"
    )
    to_addr: Annotated[str, StringConstraints(max_length=500)] = Field(
        default="", description="邮件：收件地址，多个用逗号分隔"
    )
    smtp_server: Annotated[str, StringConstraints(max_length=200)] = Field(
        default="", description="邮件：SMTP 服务器（留空按发件地址自动识别）"
    )
    smtp_port: int | None = Field(default=None, ge=1, le=65535, description="邮件：SMTP 端口")
    server_url: UrlText = Field(default="", description="ntfy 服务器（默认 https://ntfy.sh）")
    secrets: dict[str, SecretText] = Field(
        default_factory=dict,
        description="密钥字段：飞书 / 钉钉 / 企业微信 / Slack 的 webhook_url，"
        "Telegram 的 bot_token，"
        "邮件的 password，ntfy 的 topic 与 token，Bark 的 url，通用 Webhook 的 url。"
        "不给的字段保持不变，空字符串表示清除",
    )


class NewsChannelOut(BaseModel):
    enabled: bool
    msg_type: Literal["markdown", "text"]
    chat_id: str
    from_addr: str
    to_addr: str
    smtp_server: str
    smtp_port: int | None
    server_url: str
    secrets_set: list[str] = Field(description="已经设置了的密钥字段（值不回传）")


class NewsSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(
        ge=0, description="GET 返回的 version（还没保存过是 0）；不一致时 409 VERSION_CONFLICT"
    )
    enabled: bool
    push_times: list[PushTime] = Field(
        min_length=1, max_length=3, description="每天推送的时间，半小时为单位"
    )
    briefing_mode: BriefingMode = "trendradar"
    agent_instance_id: UUID | None = Field(default=None, description="方案 B 写早报的智能体实例")
    platforms: list[PlatformId] = Field(default_factory=list, max_length=11)
    newsnow_api_url: UrlText = Field(default="", description="自建 NewsNow 的地址；留空用公共接口")
    feeds: list[NewsFeedIn] = Field(default_factory=list, max_length=30)
    topics: list[NewsTopicIn] = Field(default_factory=list, max_length=20)
    exclusions: list[Word] = Field(default_factory=list, max_length=100)
    filter_method: FilterMethod = "keyword"
    ai_model: AiModel = ""
    ai_api_base: UrlText = ""
    ai_api_key: SecretText | None = Field(
        default=None, description="不给表示不改；空字符串表示清除"
    )
    ai_analysis: bool = True
    ai_translation: bool = False
    channels: dict[ChannelId, NewsChannelIn] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique(self) -> "NewsSettingsIn":
        if len(set(self.push_times)) != len(self.push_times):
            raise ValueError("推送时间不能重复")
        if len(set(self.platforms)) != len(self.platforms):
            raise ValueError("平台不能重复")
        if len({f.id for f in self.feeds}) != len(self.feeds):
            raise ValueError("RSS 源的 ID 不能重复")
        if len({t.name for t in self.topics}) != len(self.topics):
            raise ValueError("主题名不能重复")
        if self.briefing_mode == "agent" and self.agent_instance_id is None:
            raise ValueError("方案 B 要选一个智能体实例")
        return self


class NewsPlatformChoice(BaseModel):
    id: PlatformId
    name: str


class NewsTopicLearned(BaseModel):
    name: str
    score: int = Field(description="最近 60 天：收藏数 − 不感兴趣数")
    max_items: int = Field(description="设置里的每天条数")
    adjusted_max_items: int = Field(description="按偏好调整后写进配置的条数")


class NewsSourceSuggestion(BaseModel):
    source_type: SourceType
    source_id: str
    source_name: str
    dismisses: int
    favorites: int


class NewsLearnedOut(BaseModel):
    computed_at: datetime | None
    topics: list[NewsTopicLearned]
    source_suggestions: list[NewsSourceSuggestion] = Field(
        description="被“不感兴趣”很多次（≥ 5 次且多于收藏）的来源：建议关掉，不自动关"
    )


class NewsSettingsOut(BaseModel):
    workspace_id: UUID
    enabled: bool
    timezone: str = Field(
        description="时区（部署时设置：.env 的 TRENDRADAR_TZ，与 TrendRadar 容器一致；网页上只读）"
    )
    push_times: list[str]
    briefing_mode: BriefingMode
    agent_instance_id: UUID | None
    platforms: list[str]
    newsnow_api_url: str
    feeds: list[NewsFeedIn]
    topics: list[NewsTopicIn]
    exclusions: list[str]
    filter_method: FilterMethod
    ai_model: str
    ai_api_base: str
    ai_api_key_set: bool
    ai_analysis: bool
    ai_translation: bool
    channels: dict[ChannelId, NewsChannelOut]
    version: int
    config_rev: int = Field(description="生成配置文件的次数")
    rendered_at: datetime | None = Field(description="最近一次写入配置文件的时间")
    render_error: str = Field(description="最近一次写入失败的原因；成功为空")
    updated_at: datetime | None
    deployed: bool = Field(description="这个部署有没有 TrendRadar")
    taken_by_other: bool = Field(description="另一个工作空间已经启用了早报（一个部署只能有一个）")
    platform_choices: list[NewsPlatformChoice]
    learned: NewsLearnedOut


class NewsStatusOut(BaseModel):
    deployed: bool
    enabled: bool = Field(description="当前工作空间是否启用了早报")
    online: bool | None = Field(description="TrendRadar 能否连通；没有部署或没启用时为 null")
    latest_data_date: str | None = Field(description="最近一次抓到数据的日期")
    error: str | None
    latest_briefing_at: datetime | None
    rendered_at: datetime | None
    render_error: str


class NewsBriefingOut(BaseModel):
    id: UUID
    local_date: date
    slot: str = Field(description="推送时间 HH:MM；方案 A 收到时不在推送时间段里则为空")
    source: BriefingSource
    title: str
    content: str = Field(description="Markdown；按安全模式渲染（不执行 HTML）")
    status: BriefingStatus
    parts: int
    total_parts: int | None
    error: str
    task_id: UUID | None
    created_at: datetime
    updated_at: datetime


class NewsBriefingSummary(BaseModel):
    id: UUID
    local_date: date
    slot: str
    source: BriefingSource
    title: str
    status: BriefingStatus
    excerpt: str = Field(description="正文开头（纯文字，约 200 字）")
    created_at: datetime


class NewsBriefingPage(BaseModel):
    items: list[NewsBriefingSummary]
    next_cursor: str | None


class NewsItemOut(BaseModel):
    title: str
    url: str
    mobile_url: str | None
    source_type: SourceType
    source_id: str
    source_name: str
    rank: int | None
    time: str | None = Field(description="抓取或发布时间（TrendRadar 的本地时间文字）")
    summary: str | None
    topic: str | None = Field(description="命中的兴趣主题")
    favorite_id: UUID | None = Field(description="当前用户收藏了这条时是收藏记录的 ID")
    dismissed: bool


class NewsItemList(BaseModel):
    items: list[NewsItemOut]
    unavailable: bool = Field(description="TrendRadar 暂时连不上")
    message: str | None = Field(description="没有数据、暂时不可用等说明")


class NewsTopicItems(BaseModel):
    name: str
    items: list[NewsItemOut]


class NewsTodayOut(BaseModel):
    briefing: NewsBriefingOut | None
    topics: list[NewsTopicItems]
    unavailable: bool
    message: str | None


class NewsFeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FeedbackKind
    url: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    title: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    source_type: SourceType
    source_id: Annotated[str, StringConstraints(max_length=100)] = ""
    source_name: Annotated[str, StringConstraints(max_length=100)] = ""
    topic: Annotated[str, StringConstraints(max_length=30)] = ""
    exclude_word: Word | None = Field(
        default=None, description="不感兴趣时顺手加进全局排除词（只有 owner 能用）"
    )

    @field_validator("url")
    @classmethod
    def _http(cls, value: str) -> str:
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("必须是 http(s) 链接")
        return value


class NewsFeedbackOut(BaseModel):
    id: UUID
    kind: FeedbackKind
    url: str
    title: str
    source_type: SourceType
    source_id: str
    source_name: str
    topic: str
    created_at: datetime


class NewsFeedbackPage(BaseModel):
    items: list[NewsFeedbackOut]
    next_cursor: str | None


class NewsPushResult(BaseModel):
    channel: str
    ok: bool
    detail: str


class NewsPushTestOut(BaseModel):
    results: list[NewsPushResult]
