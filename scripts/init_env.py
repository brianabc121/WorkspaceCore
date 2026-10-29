#!/usr/bin/env python3
"""从 .env.example 生成 .env，并把占位符替换成随机值（只用 Python 标准库）。

- .env 不存在：按 .env.example 生成，占位符（CHANGE_ME、GENERATE_…）替换为随机值；
- .env 已存在：已有的值一律不改，只把 .env.example 里新增、而 .env 里还没有的变量追加到末尾
  （例如升级后新增的 SETUP_TOKEN）；
- 生成的密码只含字母和数字，可以直接放进连接串；本机运行用的 DATABASE_URL 会同步使用生成的数据库密码。

用法：python3 scripts/init_env.py   （make env / make up 会自动调用）
"""

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"

PLACEHOLDER = "CHANGE_ME"


def random_value(key: str) -> str:
    if key == "CREDENTIAL_ENCRYPTION_KEY":
        return secrets.token_hex(32)  # 32 字节，十六进制表示
    return secrets.token_hex(24)


def is_placeholder(value: str) -> bool:
    return value == PLACEHOLDER or value.startswith("GENERATE_")


def parse(line: str) -> tuple[str, str] | None:
    """返回 (KEY, VALUE)；注释、空行返回 None。"""
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    key, sep, value = line.partition("=")
    return (key.strip(), value) if sep else None


def render(lines: list[str]) -> tuple[list[str], dict[str, str]]:
    """替换占位符，返回新的行与生成的值。"""
    generated: dict[str, str] = {}
    out = []
    for line in lines:
        entry = parse(line)
        if entry and is_placeholder(entry[1]):
            generated[entry[0]] = random_value(entry[0])
            line = f"{entry[0]}={generated[entry[0]]}"
        out.append(line)
    password = generated.get("POSTGRES_PASSWORD")
    if password:
        out = [line.replace(f":{PLACEHOLDER}@", f":{password}@") for line in out]
    return out, generated


def create() -> None:
    lines, generated = render(EXAMPLE.read_text(encoding="utf-8").splitlines())
    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    TARGET.chmod(0o600)
    print(f"已生成 {TARGET.name}（权限 600），随机填写了：{', '.join(generated) or '无'}")


def append_missing() -> None:
    current = TARGET.read_text(encoding="utf-8")
    existing = {entry[0] for line in current.splitlines() if (entry := parse(line))}
    missing = [
        line
        for line in EXAMPLE.read_text(encoding="utf-8").splitlines()
        if (entry := parse(line)) and entry[0] not in existing
    ]
    if not missing:
        print(f"{TARGET.name} 已存在且变量齐全，保持不变")
        return
    lines, _ = render(missing)
    separator = "" if current.endswith("\n") else "\n"
    block = "\n".join(["", "# 以下变量由 make env 补充", *lines]) + "\n"
    TARGET.write_text(current + separator + block, encoding="utf-8")
    added = ", ".join(entry[0] for line in lines if (entry := parse(line)))
    print(f"{TARGET.name} 已存在，已补充缺少的变量：{added}")


def main() -> int:
    if TARGET.exists():
        append_missing()
    else:
        create()
    return 0


if __name__ == "__main__":
    sys.exit(main())
