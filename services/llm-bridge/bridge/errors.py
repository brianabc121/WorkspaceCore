"""管理器给调用方的错误（engine 和长时间任务共用，免得两个模块互相导入）。"""

from __future__ import annotations

from typing import Any


class BridgeError(Exception):
    """details：给调用方的补充（例如 retry_after_seconds），和 code、message 一起放在错误里。"""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
