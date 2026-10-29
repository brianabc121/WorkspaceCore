#!/usr/bin/env python3
"""从 .env.example 生成 .env，并把占位符替换成随机值（只用 Python 标准库）。

- .env 已存在时不做任何修改，避免覆盖已经在用的密码；
- 只替换值为占位符（CHANGE_ME、GENERATE_…）的项；生成的密码只含字母和数字，可以直接放进连接串；
- 本机运行时用的 DATABASE_URL 会同步使用生成的数据库密码。

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


def main() -> int:
    if TARGET.exists():
        print(f"{TARGET.name} 已存在，保持不变")
        return 0
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    out = []
    for line in lines:
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and not line.lstrip().startswith("#") and (
            value == PLACEHOLDER or value.startswith("GENERATE_")
        ):
            value = random_value(key)
            values[key] = value
            line = f"{key}={value}"
        out.append(line)
    text = "\n".join(out) + "\n"
    if "POSTGRES_PASSWORD" in values:
        text = text.replace(f":{PLACEHOLDER}@", f":{values['POSTGRES_PASSWORD']}@")
    TARGET.write_text(text, encoding="utf-8")
    TARGET.chmod(0o600)
    print(f"已生成 {TARGET.name}（权限 600），随机填写了：{', '.join(values) or '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
