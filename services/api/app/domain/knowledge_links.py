"""笔记正文里的 [[链接]]（docs/v2/KNOWLEDGE_PLAN.md 3.3）。

支持的写法：
- `[[标题]]`、`[[标题|显示文字]]`、`[[标题#小节]]`；
- `[[知识库名/标题]]`：只在那个知识库里找；知识库名对不上时把整段当作标题；
- `[[task:<id>]]`、`[[project:<id>]]`、`[[bookmark:<id>]]`：链接工作台里的任务、项目、书签。

代码块和行内代码里的 [[…]] 不算链接。
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass

MAX_LINKS = 500
_FENCE = re.compile(r"(^|\n)(```|~~~)[^\n]*\n.*?(\n\2[^\n]*(?=\n|$)|$)", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_LINK = re.compile(r"\[\[([^\[\]\n]{1,300})\]\]")
_RESOURCE = re.compile(r"^(task|project|bookmark):([0-9a-fA-F-]{36})$")


def normalize(text: str) -> str:
    """标题的比较用形式：NFKC、去首尾空白、连续空白变一个空格、转小写。"""
    return " ".join(unicodedata.normalize("NFKC", text).split()).lower()


@dataclass(frozen=True)
class LinkRef:
    text: str
    key: str
    space_key: str | None
    rest_key: str | None
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None


def strip_code(body: str) -> str:
    return _INLINE_CODE.sub("", _FENCE.sub("\n", body))


def parse(body: str) -> list[LinkRef]:
    """按出现顺序解析，去重，最多 MAX_LINKS 个。"""
    seen: set[tuple[str, str | None]] = set()
    refs: list[LinkRef] = []
    for match in _LINK.finditer(strip_code(body)):
        target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
        if not target:
            continue
        resource = _RESOURCE.match(target)
        if resource:
            try:
                rid = uuid.UUID(resource.group(2))
            except ValueError:
                continue
            ident = (f"{resource.group(1)}:{rid}", None)
            if ident in seen:
                continue
            seen.add(ident)
            refs.append(LinkRef(target, ident[0], None, None, resource.group(1), rid))
        else:
            key = normalize(target)
            space_key = rest_key = None
            if "/" in target:
                prefix, rest = target.split("/", 1)
                if prefix.strip() and rest.strip():
                    space_key, rest_key = normalize(prefix), normalize(rest)
            ident = (key, None)
            if ident in seen:
                continue
            seen.add(ident)
            refs.append(LinkRef(target, key, space_key, rest_key))
        if len(refs) >= MAX_LINKS:
            break
    return refs


def snippet(body: str, title: str, *, width: int = 120) -> str:
    """反向链接显示的上下文：正文里第一次出现 [[标题 的那一段，压成一行。"""
    plain = strip_code(body)
    index = plain.find("[[" + title)
    if index < 0:
        lowered = plain.lower()
        index = lowered.find("[[" + title.lower())
    if index < 0:
        return ""
    start = max(0, index - width // 2)
    end = min(len(plain), index + len(title) + 4 + width // 2)
    text = " ".join(plain[start:end].split())
    return ("…" if start > 0 else "") + text + ("…" if end < len(plain) else "")
