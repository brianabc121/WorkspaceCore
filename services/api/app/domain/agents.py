"""OpenClaw 实例与密钥库（V2 OC-2）。

约定见 docs/decisions/2026-10-31-v2-oc1-contract.md 第 3、4、7 节。这里先放加密用途（context），
密文挪到别的记录、别的版本上都会解密失败；服务与接口随 OC-2 的后续提交补上。
"""

import uuid


def agent_secret_context(secret_id: uuid.UUID, value_version: int) -> str:
    return f"agent_secret:{secret_id}:{value_version}"


def agent_ca_context(ca_id: uuid.UUID) -> str:
    return f"agent_ca:{ca_id}"
