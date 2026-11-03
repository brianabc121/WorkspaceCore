"""写进知识库之前的密钥扫描（docs/v2/KNOWLEDGE_PLAN.md 3.8 第 3 条；AGENTS.md 第 8 节第 6 条）。

命中就拒绝保存：原文不入库、不进日志，只告诉调用方是哪一类（例如“GitHub 令牌”）。
规则偏保守：宁可误拦，也不让明文密钥进入会被所有智能体读取的知识库。
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class _Rule:
    kind: str
    label: str
    pattern: re.Pattern[str]


_RULES: tuple[_Rule, ...] = (
    _Rule("private_key", "私钥", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    _Rule(
        "github_token",
        "GitHub 令牌",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    ),
    _Rule("aws_key", "AWS 访问密钥", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    _Rule(
        "model_key", "模型服务的 API 密钥", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}")
    ),
    _Rule("slack_token", "Slack 令牌", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    _Rule("workspace_token", "工作台实例令牌", re.compile(r"\bmwa_[A-Za-z0-9_\-]{20,}")),
    _Rule(
        "jwt",
        "JWT 令牌",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ),
    _Rule("bearer", "Bearer 令牌", re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{20,}")),
    _Rule(
        "url_credentials",
        "链接里的用户名和密码",
        re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://[^/\s:@]{1,64}:[^/\s@]{3,}@[^\s]+"),
    ),
    _Rule(
        "assignment",
        "密码或密钥的明文",
        re.compile(
            r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
            r"client[_-]?secret|密码|口令|令牌|密钥)\s*[:=：]\s*[\"'`]?"
            # 取值：至少 8 个非空白字符，排除占位写法（${…}、<…>、[…]、***、见密钥库…）
            r"(?![$<\[*（(]|见|参见|存在|放在)[^\s\"'`]{8,}"
        ),
    ),
)


def scan(*texts: str) -> list[str]:
    """返回命中的类别说明（去重、按规则顺序）；没有命中返回空列表。"""
    found: list[str] = []
    for rule in _RULES:
        if any(rule.pattern.search(text) for text in texts if text):
            found.append(rule.label)
    return found
