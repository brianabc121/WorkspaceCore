"""结构化 JSON 日志：每行一个 JSON 对象，自动带上当前请求的 request_id。

调用方通过 `extra={...}` 附加字段；不要把密钥、Token、Cookie 或完整的第三方 payload 放进日志。
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# LogRecord 自带的属性不当作附加字段输出；color_message 是 uvicorn 附带的终端着色版本
_RESERVED = set(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        payload.update({k: v for k, v in vars(record).items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # uvicorn、arq 自带的输出并入同一个 JSON 输出；访问日志由 request_context 中间件记录
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "arq"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True


def dict_config(level: str) -> dict[str, object]:
    """logging.config.dictConfig 格式的同一套配置，供 `arq --custom-log-dict` 使用。"""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": JsonFormatter}},
        "handlers": {
            "stdout": {"class": "logging.StreamHandler", "formatter": "json", "stream": sys.stdout}
        },
        "root": {"handlers": ["stdout"], "level": level},
    }
