#!/usr/bin/env python3
"""从 .env.example 生成 .env，并把占位符替换成随机值（只用 Python 标准库）。

- .env 不存在：按 .env.example 生成，占位符（CHANGE_ME、GENERATE_…）替换为随机值；
- .env 已存在：已有的值一律不改，只把 .env.example 里新增、而 .env 里还没有的变量追加到末尾
  （例如升级后新增的 SETUP_TOKEN）；
- 生成的密码只含字母和数字，可以直接放进连接串；
  本机运行用的 DATABASE_URL 会同步使用生成的数据库密码；
- 最后检查 .env：还留着占位符（例如用 cp .env.example .env 复制出来的）、
  加密密钥不是 64 位十六进制、生产环境的会话密钥太短、用了 Windows 换行时，
  说明问题并以非零状态退出，make up 不再继续构建
  （否则要等镜像构建完，api 才在启动时拒绝这些配置）。

用法：python3 scripts/init_env.py   （make env / make up 会自动调用）
"""

import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"

PLACEHOLDER = "CHANGE_ME"


# 要求 64 位十六进制的变量（32 字节）
HEX32_KEYS = {"CREDENTIAL_ENCRYPTION_KEY", "OPENIM_GARAGE_RPC_SECRET", "OPENIM_S3_SECRET_KEY"}
HEX64 = re.compile(r"[0-9a-fA-F]{64}")
# 与 API 的启动校验一致（app/core/config.py、app/domain/credentials.py）
MIN_SESSION_SECRET = 32
MIN_CALLBACK_TOKEN = 32


def random_value(key: str) -> str:
    if key in HEX32_KEYS:
        return secrets.token_hex(32)
    if key == "OPENIM_S3_ACCESS_KEY":
        return "GK" + secrets.token_hex(12)  # Garage 的访问密钥 ID 格式
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


def clean(value: str) -> str:
    """去掉值两边的引号；不带引号的值去掉行内注释（与 Compose 读取 .env 的方式一致）。"""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value.split(" #", 1)[0].strip()


def problems(text: str) -> list[str]:
    """检查 .env 里会让服务拒绝启动、或者明显不安全的配置；返回问题说明，没有问题时为空。"""
    found: list[str] = []
    if "\r" in text:
        found.append(
            '用了 Windows 换行（CRLF），值的末尾会多出 \\r：运行 sed -i "s/\\r$//" .env 转换'
        )
    values = {entry[0]: clean(entry[1]) for line in text.splitlines() if (entry := parse(line))}
    left = [
        key for key, value in values.items() if is_placeholder(value) or f":{PLACEHOLDER}@" in value
    ]
    if left:
        found.append(f"这些变量还是 .env.example 里的占位符，没有生成随机值：{', '.join(left)}")
    hex_hint = '用 python3 -c "import secrets; print(secrets.token_hex(32))" 生成'
    key = values.get("CREDENTIAL_ENCRYPTION_KEY", "")
    if key and "CREDENTIAL_ENCRYPTION_KEY" not in left and not HEX64.fullmatch(key):
        found.append(
            f"CREDENTIAL_ENCRYPTION_KEY 必须是 64 位十六进制"
            f"（现在是 {len(key)} 个字符），{hex_hint}"
        )
    previous = [
        part.strip() for part in values.get("CREDENTIAL_ENCRYPTION_KEYS_PREVIOUS", "").split(",")
    ]
    if any(part and not HEX64.fullmatch(part) for part in previous):
        found.append(
            "CREDENTIAL_ENCRYPTION_KEYS_PREVIOUS 里的每个旧密钥都必须是 64 位十六进制（逗号分隔）"
        )
    secret = values.get("SESSION_SECRET", "")
    if (
        values.get("APP_ENV") == "production"
        and "SESSION_SECRET" not in left
        and len(secret) < MIN_SESSION_SECRET
    ):
        found.append(
            f"生产环境（APP_ENV=production）的 SESSION_SECRET 至少 {MIN_SESSION_SECRET} 个字符"
        )
    token = values.get("OPENIM_CALLBACK_PATH_TOKEN", "")
    if token and "OPENIM_CALLBACK_PATH_TOKEN" not in left and len(token) < MIN_CALLBACK_TOKEN:
        found.append(f"OPENIM_CALLBACK_PATH_TOKEN 至少 {MIN_CALLBACK_TOKEN} 个字符")
    return found


HOW_TO_FIX = """处理方法：
  - 全新安装、还没有任何数据：把 .env 改名备份（mv .env .env.old），重新运行 make env，
    再把改过的设置（APP_ENV、PUBLIC_BASE_URL、COMPOSE_FILE、SITE_DOMAIN、ACME_EMAIL 等）
    抄回新的 .env。
    如果已经 make up 过，PostgreSQL 数据卷已经用旧密码初始化，还要先 docker compose down -v
    （会删除本项目的全部数据卷，只在确认没有数据时这样做）。
  - 已经在使用：只改上面列出的几项。CREDENTIAL_ENCRYPTION_KEY 已经加密过凭证时不能直接换，
    按 docs/deployment.md 第 6.1 节（加密密钥轮换）操作。
详见 docs/deploy-guide.md 第 4 步与“常见问题”。"""


def main() -> int:
    if TARGET.exists():
        append_missing()
    else:
        create()
    found = problems(TARGET.read_text(encoding="utf-8"))
    if found:
        print(f"{TARGET.name} 有问题，服务会拒绝启动或不安全（make up 已停止）：", file=sys.stderr)
        for item in found:
            print(f"  - {item}", file=sys.stderr)
        print(HOW_TO_FIX, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
