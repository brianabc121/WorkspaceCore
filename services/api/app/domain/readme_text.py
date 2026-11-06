"""README 的正文处理（T-0122，docs/v2/AGENT_MESSAGING_PLAN.md 第 9 节）：纯函数，不碰数据库。

- 自动部分用标记包起来：`<!-- myws:auto 成员 -->` … `<!-- /myws:auto -->`；
  工作台更新时只替换标记之间的内容，其他文字不动；有人删掉了某个标记，那一块就不再更新（9.4）。
- 智能体用 `readme_update` 只改某一个非自动的部分（按 `## 标题` 找），替换或追加（9.6）。
"""

import re
from collections.abc import Mapping

OPEN = "<!-- myws:auto {name} -->"
CLOSE = "<!-- /myws:auto -->"
_BLOCK = re.compile(
    r"<!-- myws:auto (?P<name>[^<>\n]+?) -->\n?(?P<body>.*?)\n?<!-- /myws:auto -->", re.S
)
_HEADING = re.compile(r"^## +(?P<title>.+?)[ \t]*$", re.M)
MAX_SECTION = 20_000


class SectionError(ValueError):
    """改不了这一部分（自动部分、标题不对、太长）。code 给工具返回的错误码用。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def block(name: str, content: str) -> str:
    """一个自动部分：标记 + 内容 + 结束标记。"""
    return f"{OPEN.format(name=name)}\n{content.strip()}\n{CLOSE}"


def auto_names(body: str) -> list[str]:
    """正文里还在的自动部分（按出现顺序）。"""
    return [m.group("name").strip() for m in _BLOCK.finditer(body)]


def merge(body: str, sections: Mapping[str, str]) -> str:
    """只替换标记之间的内容；正文里没有的标记不补回去，sections 里没有的块不动。"""

    def replace(match: re.Match[str]) -> str:
        name = match.group("name").strip()
        if name not in sections:
            return match.group(0)
        return block(name, sections[name])

    return _BLOCK.sub(replace, body)


def headings(body: str) -> list[str]:
    return [m.group("title").strip() for m in _HEADING.finditer(body)]


def _span(body: str, title: str) -> tuple[int, int] | None:
    """`## title` 这一部分的正文范围（标题行之后到下一个 `## ` 之前）。"""
    matches = list(_HEADING.finditer(body))
    for index, match in enumerate(matches):
        if match.group("title").strip() == title:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
            return match.end(), end
    return None


def section(body: str, title: str) -> str | None:
    span = _span(body, title)
    return body[span[0] : span[1]].strip() if span else None


def is_auto(body: str, title: str) -> bool:
    text = section(body, title)
    return text is not None and _BLOCK.search(text) is not None


def edit_section(body: str, title: str, text: str, mode: str) -> str:
    """替换（replace）或追加（append）一个非自动部分；没有这一部分就加在最后。"""
    title = " ".join(title.split())
    if not title or len(title) > 60 or "#" in title:
        raise SectionError("VALIDATION_FAILED", "部分标题要 1 ~ 60 个字，不含 #")
    if _BLOCK.search(text) or "<!-- myws:auto" in text or CLOSE in text:
        raise SectionError("VALIDATION_FAILED", "内容里不能有自动部分的标记")
    if _HEADING.search(text):
        raise SectionError("VALIDATION_FAILED", "内容里不能有二级标题（## ），每次只改一个部分")
    if len(text) > MAX_SECTION:
        raise SectionError("VALIDATION_FAILED", f"一个部分最多 {MAX_SECTION} 个字")
    span = _span(body, title)
    if span is None:
        return f"{body.rstrip()}\n\n## {title}\n\n{text.strip()}\n"
    start, end = span
    current = body[start:end]
    if _BLOCK.search(current):
        raise SectionError("AUTO_SECTION", f"“{title}”是自动更新的部分，由工作台维护，不能改")
    if mode == "append" and current.strip():
        new = f"{current.strip()}\n\n{text.strip()}"
    else:
        new = text.strip()
    tail = "\n\n" if end < len(body) else "\n"
    return f"{body[:start]}\n\n{new}{tail}{body[end:].lstrip()}"


def checklist(description: str) -> str:
    """描述里的清单（`- [ ]` / `- [x]`）原样带过来做验收标准。"""
    lines = [line.rstrip() for line in description.splitlines()]
    items = [line for line in lines if re.match(r"^\s*[-*] \[[ xX]\] ", line)]
    return "\n".join(items)


def first_paragraph(text: str, limit: int = 600) -> str:
    for part in re.split(r"\n\s*\n", text.strip()):
        part = part.strip()
        if part:
            return part if len(part) <= limit else part[: limit - 1] + "…"
    return ""


def split_blocks(body: str) -> list[tuple[str | None, str]]:
    """把正文切成（自动部分名或 None，文字）的片段，页面上给自动部分加角标用。"""
    out: list[tuple[str | None, str]] = []
    cursor = 0
    for match in _BLOCK.finditer(body):
        if match.start() > cursor:
            out.append((None, body[cursor : match.start()]))
        out.append((match.group("name").strip(), match.group("body")))
        cursor = match.end()
    if cursor < len(body):
        out.append((None, body[cursor:]))
    return out
